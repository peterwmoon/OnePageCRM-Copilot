@echo off
cd /d "H:\ClaudeCode\OnePageCRM-Copilot"
start http://localhost:8765
python -m http.server 8765
