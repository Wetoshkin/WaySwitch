# Пакет .rpm для Fedora (в том числе Silverblue/Kinoite через rpm-ostree) и
# openSUSE. Собирается tools/build_rpm.sh; файлы раскладывает tools/stage.sh —
# тот же, что для .deb и AUR.
#
# Зависимости записаны с «или»: в Fedora GTK и libadwaita — пакеты gtk4 и
# libadwaita, в openSUSE их typelib-ы называются typelib-1_0-*.

Name:           wayswitch
Version:        %{ws_version}
Release:        1%{?dist}
Summary:        Fixes words typed in the wrong keyboard layout on Wayland (ru/en)
License:        MIT AND CC-BY-SA-4.0
URL:            https://github.com/Wetoshkin/WaySwitch
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-evdev
Requires:       python3-gobject
Requires:       (gtk4 or typelib-1_0-Gtk-4_0)
Requires:       (libadwaita >= 1.5 or typelib-1_0-Adw-1 >= 1.5)
Requires:       (libxkbcommon or libxkbcommon0)
Recommends:     (gnome-shell-extension-appindicator if gnome-shell)
Recommends:     (ibus if gnome-shell)

%description
WaySwitch — аналог Punto Switcher для Wayland: исправляет слова, набранные
не в той раскладке (ghbdtn → привет), сразу на пробеле, и переключает
раскладку. GNOME, KDE Plasma, Sway, Hyprland, Xfce, MATE, Cinnamon, Budgie,
LXQt.

%prep

%build

%install
bash %{ws_src}/tools/stage.sh %{buildroot} /usr/lib/wayswitch
# Лицензии и README ставит сам rpm из %%license и %%doc.
rm -rf %{buildroot}/usr/share/licenses %{buildroot}/usr/share/doc
cp %{ws_src}/LICENSE %{ws_src}/README.md .
cp %{ws_src}/wayswitch/data/LICENSE DATA-LICENSE

%post
udevadm control --reload-rules >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=input --subsystem-match=misc >/dev/null 2>&1 || :
modprobe uinput >/dev/null 2>&1 || :

%postun
udevadm control --reload-rules >/dev/null 2>&1 || :

%files
%license LICENSE DATA-LICENSE
%doc README.md
/usr/bin/wayswitch
/usr/bin/wayswitch-gui
/usr/lib/wayswitch/
/usr/lib/udev/rules.d/60-wayswitch.rules
/usr/lib/modules-load.d/wayswitch.conf
/usr/lib/systemd/user/wayswitch.service
%dir /usr/share/gnome-shell
%dir /usr/share/gnome-shell/extensions
/usr/share/gnome-shell/extensions/wayswitch@siberia.ru/
%dir /usr/share/plasma
%dir /usr/share/plasma/plasmoids
/usr/share/plasma/plasmoids/ru.siberia.wayswitch/
%config(noreplace) /etc/xdg/autostart/wayswitch-session.desktop
/usr/share/applications/ru.siberia.WaySwitch.Settings.desktop
/usr/share/icons/hicolor/scalable/apps/ru.siberia.WaySwitch.svg
/usr/share/icons/hicolor/symbolic/apps/ru.siberia.WaySwitch-symbolic.svg
/usr/share/icons/hicolor/symbolic/apps/ru.siberia.WaySwitch-paused-symbolic.svg

%changelog
* Sat Oct 24 2026 Wetoshkin <wetoshkin@gmail.com> - %{ws_version}-1
- Сборка из репозитория: история изменений — в релизах на GitHub.
