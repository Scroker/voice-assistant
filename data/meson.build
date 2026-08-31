install_data('schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml',
  install_dir: join_paths(extensiondir, 'schemas')
)

install_data('schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml',
  install_dir: join_paths(get_option('datadir'), 'glib-2.0', 'schemas')
)

install_subdir('icons',
  install_dir: extensiondir
)

install_subdir('services',
  install_dir: extensiondir
)

install_subdir('dbus',
  install_dir: extensiondir
)

blueprint_compiler = find_program('blueprint-compiler', required: true)

prefs_ui_target = custom_target('compile-prefs-blueprint',
  input: 'ui/prefs.blp',
  output: 'prefs.ui',
  command: [blueprint_compiler, 'compile', '--output', '@OUTPUT@', '@INPUT@']
)

custom_target('voice-assistant-gresource',
  input: 'schemas/org.gnome.shell.extensions.voice-assistant.gresource.xml',
  output: 'org.gnome.shell.extensions.voice-assistant.gresource',
  depends: prefs_ui_target,
  command: [find_program('glib-compile-resources'),
            '--target=@OUTPUT@',
            '--sourcedir=' + meson.current_source_dir(),
            '--sourcedir=' + meson.current_build_dir(),
            '@INPUT@'],
  install: true,
  install_dir: extensiondir
)

configure_file(
  input: 'metadata.json.in',
  output: 'metadata.json',
  copy: true,
  install: true,
  install_dir: extensiondir
)
