// Linux (tests and development only): WebRTC's desktop capturer there needs GLib/GIO and X11, which the
// prebuilt library does not link by itself. Windows links everything it needs already.
fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("linux") {
        for lib in ["gio-2.0", "gobject-2.0", "glib-2.0", "X11", "Xext", "Xfixes", "Xdamage", "Xrandr", "Xcomposite", "Xtst", "drm", "gbm"] {
            println!("cargo:rustc-link-lib=dylib={lib}");
        }
    }
}
