project('voice-assistant',
  version: '1.0.0',
  meson_version: '>= 0.53.0'
)

uuid = 'voice-assistant@mkswap.github.io'

i18n = import('i18n')
gnome = import('gnome')

extensiondir = join_paths(get_option('datadir'), 'gnome-shell', 'extensions', uuid)

subdir('data')
subdir('src')
subdir('po')

install_data('stylesheet.css', install_dir: extensiondir)

gnome.post_install(
  glib_compile_schemas: true,
  update_desktop_database: false
)

meson.add_install_script('sh', '-c', 'glib-compile-schemas "$MESON_INSTALL_DESTDIR_PREFIX/share/gnome-shell/extensions/@0@/schemas"'.format(uuid))
