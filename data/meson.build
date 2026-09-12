install_data('schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml',
  install_dir: join_paths(extensiondir, 'schemas')
)

install_data('stylesheet.css',
  install_dir: extensiondir
)

install_data('schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml',
  install_dir: join_paths(get_option('datadir'), 'glib-2.0', 'schemas')
)

install_data('services/org.local.VoiceAssistant.desktop.in',
  rename: 'org.local.VoiceAssistant.desktop',
  install_dir: join_paths(get_option('datadir'), 'applications')
)

install_data('services/org.local.VoiceAssistant.desktop.in',
  rename: 'org.local.VoiceAssistant.GUI.desktop',
  install_dir: join_paths(get_option('datadir'), 'applications')
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

install_subdir('mcp',
  install_dir: extensiondir
)

install_subdir('dependencies',
  install_dir: extensiondir
)

install_subdir('prompts',
  install_dir: extensiondir
)

install_subdir('locales',
  install_dir: extensiondir
)

install_subdir('llm',
  install_dir: extensiondir
)

install_subdir('nlu',
  install_dir: extensiondir
)

install_subdir('catalog',
  install_dir: extensiondir
)

install_subdir('config',
  install_dir: extensiondir
)

blueprint_compiler = find_program('blueprint-compiler', required: false)

if blueprint_compiler.found()
  prefs_ui_target = custom_target('compile-prefs-blueprint',
    input: 'ui/prefs.blp',
    output: 'prefs.ui',
    command: [blueprint_compiler, 'compile', '--output', '@OUTPUT@', '@INPUT@']
  )

  assistant_ui_target = custom_target('compile-assistant-blueprint',
    input: 'ui/assistant_window.blp',
    output: 'assistant_window.ui',
    command: [blueprint_compiler, 'compile', '--output', '@OUTPUT@', '@INPUT@']
  )
  ui_targets = [prefs_ui_target, assistant_ui_target]
else
  prefs_ui_target = custom_target('copy-prefs-ui',
    input: 'ui/prefs.ui',
    output: 'prefs.ui',
    command: ['cp', '@INPUT@', '@OUTPUT@']
  )
  assistant_ui_target = custom_target('copy-assistant-ui',
    input: 'ui/assistant_window.ui',
    output: 'assistant_window.ui',
    command: ['cp', '@INPUT@', '@OUTPUT@']
  )
  ui_targets = [prefs_ui_target, assistant_ui_target]
endif

gresource_target = custom_target('voice-assistant-gresource',
  input: 'schemas/org.gnome.shell.extensions.voice-assistant.gresource.xml',
  output: 'org.gnome.shell.extensions.voice-assistant.gresource',
  depends: ui_targets,
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
