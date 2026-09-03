project('voice-assistant',
  version: '1.0.0',
  meson_version: '>= 0.56.0'
)

uuid = 'voice-assistant@scroker.github.io'

i18n = import('i18n')
gnome = import('gnome')

extensiondir = join_paths(get_option('datadir'), 'gnome-shell', 'extensions', uuid)

subdir('data')
subdir('src')
subdir('po')
subdir('tests')

# stylesheet.css is installed via data/meson.build

gnome.post_install(
  glib_compile_schemas: true,
  update_desktop_database: false
)

meson.add_install_script('sh', '-c', 'glib-compile-schemas "$MESON_INSTALL_DESTDIR_PREFIX/share/gnome-shell/extensions/@0@/schemas"'.format(uuid))

gnome_extensions = find_program('gnome-extensions', required: false)
if gnome_extensions.found()
  src_root = meson.project_source_root()
  build_root = meson.project_build_root()
  custom_target('zip',
    depends: [gresource_target, prefs_ui_target],
    command: [
      'sh', '-c',
      'TMPDIR=$(mktemp -d) && ' +
      'cp -r "@0@"/src/* "$TMPDIR/" && '.format(src_root) +
      'rm -rf "$TMPDIR/daemon/venv" && ' +
      'find "$TMPDIR" -name __pycache__ -exec rm -rf {} + 2>/dev/null || true && ' +
      'cp -r "@0@"/data/icons "$TMPDIR/" && '.format(src_root) +
      'cp -r "@0@"/data/services "$TMPDIR/" && '.format(src_root) +
      'cp -r "@0@"/data/dbus "$TMPDIR/" && '.format(src_root) +
      'cp "@0@"/data/stylesheet.css "$TMPDIR/" && '.format(src_root) +
      'cp "@0@"/data/metadata.json "$TMPDIR/" && '.format(build_root) +
      'cp "@0@"/data/org.gnome.shell.extensions.voice-assistant.gresource "$TMPDIR/" && '.format(build_root) +
      'mkdir -p "$TMPDIR/schemas" && cp "@0@"/data/schemas/*.xml "$TMPDIR/schemas/" && glib-compile-schemas "$TMPDIR/schemas/" && '.format(src_root) +
      'gnome-extensions pack --force ' +
      '--extra-source=daemon ' +
      '--extra-source=gui ' +
      '--extra-source=icons ' +
      '--extra-source=services ' +
      '--extra-source=dbus ' +
      '--extra-source=org.gnome.shell.extensions.voice-assistant.gresource ' +
      '--out-dir="@0@" "$TMPDIR" && '.format(build_root) +
      'rm -rf "$TMPDIR"'
    ],
    output: uuid + '.shell-extension.zip',
    build_by_default: false
  )
endif
