#!/bin/bash

# Создаем папку приложения
INSTALL_DIR="$HOME/.local/share/wayland-switcher"
mkdir -p "$INSTALL_DIR"
cp daemon.py gui.py "$INSTALL_DIR/"

# Создаем скрипт запуска
cat << EOF > "$INSTALL_DIR/run.sh"
#!/bin/bash
# Запуск демона (нужен доступ к /dev/input, поэтому через pkexec или sudo)
# В идеале - добавить пользователя в группу input
python3 "$INSTALL_DIR/daemon.py" &
python3 "$INSTALL_DIR/gui.py"
EOF
chmod +x "$INSTALL_DIR/run.sh"

# Создаем файл автозагрузки
mkdir -p "$HOME/.config/autostart"
cat << EOF > "$HOME/.config/autostart/wayland-switcher.desktop"
[Desktop Entry]
Type=Application
Name=Wayland Switcher
Exec=$INSTALL_DIR/run.sh
Icon=preferences-desktop-keyboard
Terminal=false
Categories=Utility;
EOF

echo "Установка завершена!"
echo "Приложение будет запускаться автоматически при входе в систему."
