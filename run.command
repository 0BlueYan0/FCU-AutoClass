#!/bin/bash
cd "$(dirname "$0")" || exit 1

# Force UTF-8 so the Chinese log lines render correctly under any locale.
export PYTHONUTF8=1

if [ -x ".venv/bin/python" ]; then
    ".venv/bin/python" app.py
else
    python3 app.py
fi

echo ""
read -n 1 -s -r -p "程式已結束, 按任意鍵關閉視窗..."
echo ""
