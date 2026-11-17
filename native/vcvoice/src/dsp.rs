//! Signal processing building blocks, ported from the Python client (app/client/core/dsp.py and audio.py).
//! Everything works on 10 ms frames of 48 kHz mono audio, as f32 samples in the int16 range.

pub const SAMPLE_RATE: u32 = 48_000;
pub const FRAME: usize = 480;
pub const I16_MAX: f32 = 32767.0;

pub fn peak(samples: &[f32]) -> f32 {
    samples.iter().fold(0.0f32, |m, s| m.max(s.abs()))
}

pub fn rms_dbfs(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return -96.0;
    }
    let sum: f64 = samples.iter().map(|s| (*s as f64) * (*s as f64)).sum();
    let rms = (sum / samples.len() as f64).sqrt() as f32;
    20.0 * (rms.max(1.0) / I16_MAX).log10()
}

pub fn db(value: f32) -> f32 {
    20.0 * (value.max(1.0) / I16_MAX).log10()
}

pub fn to_i16(samples: &[f32], out: &mut [i16]) {
    for (o, s) in out.iter_mut().zip(samples) {
        *o = s.clamp(-I16_MAX, I16_MAX) as i16;
    }
}

/// 10 ms ramps: sound that starts or stops in the middle of a waveform is a click.
pub fn fade_in(samples: &mut [f32]) {
    let n = samples.len().max(2) as f32 - 1.0;
    for (i, s) in samples.iter_mut().enumerate() {
        *s *= i as f32 / n;
    }
}

pub fn fade_out(samples: &mut [f32]) {
    let n = samples.len().max(2) as f32 - 1.0;
    for (i, s) in samples.iter_mut().enumerate() {
        *s *= 1.0 - i as f32 / n;
    }
}

/// Peak limiter with a soft knee: loud is allowed, distortion is not. Gain drops at once when a peak
/// would pass the ceiling and recovers over ~80 ms; the change glides across the frame.
pub struct Limiter {
    ceiling: f32,
    pub gain: f32,
    release: f32,
}

impl Limiter {
    pub fn new(ceiling: f32) -> Self {
        Self { ceiling: ceiling * I16_MAX, gain: 1.0, release: (-10.0f32 / 80.0).exp() }
    }

    pub fn process(&mut self, samples: &mut [f32]) {
        let p = peak(samples);
        let target = if p > 0.0 { (self.ceiling / p).min(1.0) } else { 1.0 };
        let previous = self.gain;
        self.gain = if target < self.gain { target } else { target + (self.gain - target) * self.release };
        let n = samples.len().max(2) as f32 - 1.0;
        let glide = (previous - self.gain).abs() > 1e-4;
        for (i, s) in samples.iter_mut().enumerate() {
            let g = if glide { previous + (self.gain - previous) * i as f32 / n } else { self.gain };
            *s *= g;
            let a = s.abs();
            if a > self.ceiling {
                let excess = (a - self.ceiling) / (I16_MAX - self.ceiling);
                *s = s.signum() * (self.ceiling + (I16_MAX - self.ceiling) * excess.tanh());
            }
        }
    }
}

/// Slow automatic gain: brings speech towards a target loudness.
pub struct Leveler {
    target: f32,
    max_gain: f32,
    min_gain: f32,
    level_db: f32,
    pub gain: f32,
}

impl Leveler {
    pub fn new(target: f32, max_gain: f32, min_gain: f32) -> Self {
        Self { target, max_gain, min_gain, level_db: target, gain: 1.0 }
    }

    pub fn process(&mut self, samples: &mut [f32], learn: bool) {
        let level = rms_dbfs(samples);
        if learn && level > -55.0 {
            self.level_db += (level - self.level_db) * 0.02;
            let wanted = 10f32.powf((self.target - self.level_db) / 20.0).clamp(self.min_gain, self.max_gain);
            self.gain += (wanted - self.gain) * 0.05;
        }
        for s in samples.iter_mut() {
            *s *= self.gain;
        }
    }
}

/// Voice activation with an automatic threshold that follows the room's noise floor, or a fixed one.
pub struct VoiceGate {
    pub auto: bool,
    pub manual_threshold_db: f32,
    noise_floor_db: f32,
    hold: u32,
    pub level_db: f32,
}

impl VoiceGate {
    const HANGOVER_FRAMES: u32 = 30; // keep sending 300 ms after speech so word endings are not cut

    pub fn new() -> Self {
        Self { auto: true, manual_threshold_db: -45.0, noise_floor_db: -60.0, hold: 0, level_db: -96.0 }
    }

    pub fn threshold_db(&self) -> f32 {
        if !self.auto {
            return self.manual_threshold_db;
        }
        (self.noise_floor_db + 12.0).clamp(-58.0, -28.0)
    }

    pub fn process(&mut self, samples: &[f32], speech_probability: Option<f32>) -> bool {
        let level = rms_dbfs(samples);
        self.level_db = level;
        if level < self.noise_floor_db {
            self.noise_floor_db += (level - self.noise_floor_db) * 0.2;
        } else {
            self.noise_floor_db += (level - self.noise_floor_db) * 0.002;
        }
        let mut voiced = level >= self.threshold_db();
        if let Some(p) = speech_probability {
            if self.auto {
                voiced = (voiced && p >= 0.35) || p >= 0.85;
            }
        }
        if voiced {
            self.hold = Self::HANGOVER_FRAMES;
        } else if self.hold > 0 {
            self.hold -= 1;
        }
        voiced || self.hold > 0
    }
}

/// Turns a too-loud microphone down before processing: quickly on a loud peak, back up slowly.
/// A mic set too loud in Windows left the echo/noise processing no headroom (PC3, 2026-11-15).
pub struct HeadroomGuard {
    pub gain: f32,
    pub min_seen: f32,
}

pub const HOT_PEAK: f32 = 23197.0; // -3 dBFS
const HEADROOM_TARGET: f32 = 16422.0; // -6 dBFS
const HEADROOM_MIN: f32 = 0.25; // never more than -12 dB
const HEADROOM_RECOVER: f32 = 1.000_172_7; // +0.15 dB per second at 100 frames a second

impl HeadroomGuard {
    pub fn new() -> Self {
        Self { gain: 1.0, min_seen: 1.0 }
    }

    pub fn process(&mut self, samples: &mut [f32]) {
        let p = peak(samples);
        let previous = self.gain;
        if p * previous > HOT_PEAK {
            self.gain = (HEADROOM_TARGET / p).max(HEADROOM_MIN);
        } else if previous < 1.0 {
            self.gain = (previous * HEADROOM_RECOVER).min(1.0);
        }
        self.min_seen = self.min_seen.min(self.gain);
        if previous >= 0.999 && self.gain >= 0.999 {
            return;
        }
        let n = samples.len().max(2) as f32 - 1.0;
        for (i, s) in samples.iter_mut().enumerate() {
            *s *= previous + (self.gain - previous) * i as f32 / n;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(amplitude: f32) -> Vec<f32> {
        (0..FRAME).map(|i| amplitude * (2.0 * std::f32::consts::PI * 300.0 * i as f32 / 48_000.0).sin()).collect()
    }

    #[test]
    fn limiter_keeps_peaks_under_ceiling_after_attack() {
        let mut lim = Limiter::new(0.71);
        for _ in 0..20 {
            let mut f = tone(32767.0);
            lim.process(&mut f);
            assert!(peak(&f) < 32767.0);
        }
        let mut f = tone(32767.0);
        lim.process(&mut f);
        assert!(peak(&f) <= 0.72 * 32767.0);
    }

    #[test]
    fn leveler_raises_quiet_voice() {
        let mut lev = Leveler::new(-20.0, 4.0, 0.5);
        let mut last = 0.0;
        for _ in 0..400 {
            let mut f = tone(1000.0);
            lev.process(&mut f, true);
            last = peak(&f);
        }
        assert!(last > 2500.0);
    }

    #[test]
    fn gate_opens_on_voice_and_holds() {
        let mut g = VoiceGate::new();
        for _ in 0..50 {
            assert!(!g.process(&vec![0.0; FRAME], None));
        }
        assert!(g.process(&tone(8000.0), None));
        assert!(g.process(&vec![0.0; FRAME], None), "hangover");
        g.auto = false;
        g.manual_threshold_db = -10.0;
        for _ in 0..40 {
            g.process(&vec![0.0; FRAME], None);
        }
        assert!(!g.process(&tone(3000.0), None), "manual threshold above the voice keeps it closed");
    }

    #[test]
    fn headroom_guard_turns_down_hot_mic() {
        let mut h = HeadroomGuard::new();
        for _ in 0..10 {
            let mut f = tone(32000.0);
            h.process(&mut f);
        }
        assert!(h.gain < 0.6);
    }
}
