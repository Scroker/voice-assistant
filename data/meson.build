install_data('schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml',
  install_dir: join_paths(extensiondir, 'schemas')
)

install_data('schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml',
  install_dir: join_paths(get_option('datadir'), 'glib-2.0', 'schemas')
)

install_subdir('icons',
  install_dir: extensiondir
)

custom_target('voice-assistant-gresource',
  input: 'schemas/org.gnome.shell.extensions.voice-assistant.gresource.xml',
  output: 'org.gnome.shell.extensions.voice-assistant.gresource',
  command: [find_program('glib-compile-resources'),
            '--target=@OUTPUT@',
            '--sourcedir=@CURRENT_SOURCE_DIR@',
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
