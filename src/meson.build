extension_sources = [
  'extension.js',
  'prefs.js'
]

install_data(extension_sources, install_dir: extensiondir)
install_subdir('prefs', install_dir: extensiondir)
install_subdir('daemon', install_dir: extensiondir, exclude_directories: ['venv'])
install_subdir('gui', install_dir: extensiondir, exclude_directories: ['venv', '__pycache__'])

