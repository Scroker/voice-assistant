---
name: "Launch App"
intent: launch_app
tool: app_launcher
args: {}
pattern: "(?:apri|avvia|lancia)\\s+(?:il\\s+|la\\s+|le\\s+|l'|i\\s+)?((?:file\\s+manager|text\\s+editor|\\w+)(?:\\s+\\w+)?)"
param_extract: "(?:apri|avvia|lancia)\\s+(?:il\\s+|la\\s+|le\\s+|l'|i\\s+)?((?:file\\s+manager|text\\s+editor|\\w+)(?:\\s+\\w+)?)"
param_key: app_name
triggers:
  - "apri firefox"
  - "lancia il browser"
  - "apri il terminale"
  - "avvia il calendario"
  - "apri le impostazioni"
  - "apri nautilus"
  - "apri spotify"
  - "lancia l'applicazione"
---
Launch a desktop application by name.
