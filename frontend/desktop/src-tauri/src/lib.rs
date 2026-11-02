// augustharness_lib — Tauri 2 app entry
//
// Folder map:
//   src-tauri/ ← this crate: Rust shell + Node supervisor
//   webview content comes from ../../web-dist during desktop builds
//
// Crate-wide lint suppressions: after the snake_case → camelCase migration
// we keep these identifiers as `camelCase` to align with the rest of the
// codebase. Function names, struct fields, and locals are intentional —
// not to be re-renamed.

#![allow(non_camel_case_types)]
#![allow(non_snake_case)]

mod backend;
mod tray;

use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Emitter, Manager, RunEvent};

/// Set by `confirm_quit` when the webview answered a `quit-requested` event.
/// The window-close path clears it and then watches for an answer; if the
/// webview never replies — because it is stuck behind the backend bootstrap
/// gate, whose full-screen overlay covers the confirm modal — the shell quits
/// on its own instead of leaving an uncloseable window and an orphaned backend
/// holding :8085.
static QUIT_CONFIRMED: AtomicBool = AtomicBool::new(false);

/// Set by the webview when it has actually SHOWN the quit confirm modal. The
/// close watchdog force-quits only when this never arrives — i.e. the UI is
/// stuck behind the bootstrap gate and cannot answer. Without it, a Cancelled
/// quit would be force-quit two seconds later, because the modal closing is
/// indistinguishable from the modal never appearing.
static QUIT_MODAL_ACKED: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn quit_modal_acked() {
    QUIT_MODAL_ACKED.store(true, Ordering::SeqCst);
}

/// Ask the webview to confirm a quit, and force the exit if it cannot answer.
///
/// Both window-close and tray Quit route through the confirm modal, which lives
/// in the webview. When the backend bootstrap gate covers the screen that modal
/// can never appear, and a `prevent_close()` with no fallback leaves an
/// uncloseable app — whose orphaned backend then holds :8085 and makes the next
/// launch fail the same way. The modal acknowledges the moment it shows, so a
/// quit the user actually cancelled is never overridden; only a silent webview
/// is. The forced exit runs the normal path, whose `RunEvent::Exit` handler
/// reaps the backend child tree.
pub(crate) fn request_quit_with_watchdog(app: &AppHandle) {
    QUIT_CONFIRMED.store(false, Ordering::SeqCst);
    QUIT_MODAL_ACKED.store(false, Ordering::SeqCst);
    let _ = app.emit("quit-requested", ());
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(2000));
        if !QUIT_CONFIRMED.load(Ordering::SeqCst) && !QUIT_MODAL_ACKED.load(Ordering::SeqCst) {
            handle.exit(0);
        }
    });
}

/// Confirmed quit from the webview modal (tray Quit → quit-requested → UI).
/// Stops the local backend first so the next launch starts a fresh process.
#[tauri::command]
fn confirm_quit(app: AppHandle) {
    QUIT_CONFIRMED.store(true, Ordering::SeqCst);
    backend::stopBackendOnQuit(&app);
    app.exit(0);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Task Manager / taskbar identity: claim the bundle identifier as this
    // process's AppUserModelID BEFORE any window exists. The WebView2 loader
    // propagates it to the msedgewebview2 child processes, so Task Manager
    // groups them under the "August Harness" app node instead of surfacing a bare
    // "Microsoft Edge WebView2" background entry.
    #[cfg(windows)]
    unsafe {
        use windows::core::w;
        use windows::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID;
        let _ = SetCurrentProcessExplicitAppUserModelID(w!("com.august.harness"));
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Single-instance guard: a second launch must not attach to the
            // first instance's backend (quitting either would kill the shared
            // process mid-request). The guard logs and exits; the first
            // instance keeps running untouched.
            // The user-state directory must exist under the new bundle
            // identifier before anything writes into it, and the instance lock
            // below is the first writer.
            backend::migrateAppDataRoot(app.handle());
            if !backend::acquireInstanceLock(app.handle()) {
                app.handle().exit(0);
                return Ok(());
            }
            // Always register supervisor state so restart_proxy can run even
            // when the first spawn attempt fails (missing venv, etc.).
            app.manage(backend::BackendProcess(
                std::sync::Mutex::new(None),
                std::sync::Mutex::new(None),
            ));
            app.manage(backend::BackendSetupStatus(std::sync::Mutex::new(
                backend::SetupPhase {
                    phase: "starting".into(),
                    detail: Some("Starting backend…".into()),
                },
            )));

            // Clear leftover quiet-update marker from NSIS POSTINSTALL.
            if let Ok(exe) = std::env::current_exe() {
                if let Some(dir) = exe.parent() {
                    let marker = dir.join(".august-update-complete");
                    let _ = std::fs::remove_file(marker);
                }
            }

            // Start the backend off the UI thread so the webview can show a
            // setup overlay while first-launch bootstrap / uvicorn warm-up runs.
            // Then keep a watchdog that restarts if the process dies.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                backend::ensureRunning(&handle);
                backend::watchBackend(&handle);
            });

            // 2) Install the system tray (Show / Hide / Quit)
            tray::install(app.handle())?;

            // 3) WebView2 hardening: wry leaves the default browser context
            // menu enabled (Back / Reload / Save as / Print / Inspect) and
            // Tauri's config doesn't expose the toggle, so turn it off on the
            // controller directly. Right-click then behaves like a native app.
            // (Zoom controls and the link-hover status bar are already off —
            // wry ties them to `zoomHotkeysEnabled`, which defaults false.)
            #[cfg(windows)]
            if let Some(main) = app.get_webview_window("main") {
                let _ = main.with_webview(|platform| unsafe {
                    if let Ok(core) = platform.controller().CoreWebView2() {
                        if let Ok(settings) = core.Settings() {
                            let _ = settings.SetAreDefaultContextMenusEnabled(false);
                        }
                    }
                });
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Window X / Alt+F4 → full quit (same confirm modal as tray Quit).
            // Hide-to-tray remains available from the tray menu only.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.show();
                let _ = window.set_focus();
                request_quit_with_watchdog(window.app_handle());
            }
        })
        .invoke_handler(tauri::generate_handler![
            backend::restart_proxy,
            backend::restart_to_apply,
            backend::self_edit_status,
            backend::set_self_edit_enabled,
            backend::proxy_status,
            backend::select_directory,
            backend::read_file_base64,
            backend::reveal_in_folder,
            backend::copy_files_to_dir,
            backend::backend_last_error,
            backend::backend_setup_status,
            backend::sync_backend_deps,
            backend::stop_backend_for_update,
            backend::schedule_post_update_relaunch,
            backend::download_release_installer,
            backend::downloaded_installer,
            backend::cancel_update_download,
            backend::launch_installer_and_exit,
            backend::local_api_token,
            backend::set_lan_access,
            confirm_quit,
            quit_modal_acked,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // Any exit path (tray Quit, updater, taskkill) must stop the
            // backend and release resources\python\*.pyd locks.
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                backend::stopBackendOnQuit(app_handle);
            }
        });
}
