import struct

import pytest

from app.client.audio_mixer import AudioMixer
from app.client.e2ee import ChannelE2EE, E2EEIdentity
from app.e2ee_crypto import media_key_id
from app.media_crypto import ReplayProtectedCrypto
from app.server.e2ee_state import E2EEState


def test_rejoin_rotates_key_and_recipient_can_decrypt():
    sender, recipient = E2EEIdentity({}), E2EEIdentity({})
    server = E2EEState()
    server.set_public_key(1, sender.public_key, sender.fingerprint)
    server.set_public_key(2, recipient.public_key, recipient.fingerprint)
    receiver = ChannelE2EE()
    keys = []
    for _ in range(5):
        session = ChannelE2EE()
        key_id, key = session.ensure_outgoing_key()
        keys.append(key)
        envelopes = session.envelopes_for(identity=sender, channel_id=7, sender_id=1,
            users=[{'user_id': 2, 'public_key': recipient.public_key}])
        server.set_sender_key(7, 1, key_id, envelopes, {2})
        state = server.channel_state(7, {1, 2}, 2)
        receiver.load_sender_keys(identity=recipient, channel_id=7, recipient_id=2,
            users_by_id={1: {'public_key': sender.public_key}}, key_sets=state['key_sets'])
        assert receiver.sender_keys[1] == key
        assert receiver.sender_key_ids[1] == media_key_id(key)
    assert len(set(keys)) == 5


def test_simultaneous_speakers_are_mixed_and_volume_is_applied_at_output():
    mixer = AudioMixer(640)
    mixer.put(1, struct.pack('<h', 1000) * 320)
    mixer.put(2, struct.pack('<h', 2000) * 320)
    pcm, audible = mixer.render(lambda uid: False, lambda uid: 50 if uid == 1 else 100)
    assert pcm == struct.pack('<h', 2500) * 320
    assert audible == {1, 2}
    assert mixer.render(lambda uid: False, lambda uid: 100)[0] == bytes(640)


def test_mute_affects_already_queued_audio():
    mixer = AudioMixer(640)
    mixer.put(1, struct.pack('<h', 1000) * 320)
    pcm, audible = mixer.render(lambda uid: True, lambda uid: 100)
    assert pcm == bytes(640)
    assert not audible


def test_replay_rejected_and_invalid_packet_does_not_poison_counter():
    send = ReplayProtectedCrypto(b'x' * 32, 'send')
    recv = ReplayProtectedCrypto(b'x' * 32)
    packet = send.encrypt(b'voice', b'channel')
    invalid = bytearray(packet)
    invalid[16] ^= 127
    with pytest.raises(ValueError):
        recv.decrypt(bytes(invalid), b'channel')
    assert recv.decrypt(packet, b'channel') == b'voice'
    with pytest.raises(ValueError, match='replay'):
        recv.decrypt(packet, b'channel')
