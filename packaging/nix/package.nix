{
  lib,
  python3Packages,
  wrapGAppsHook4,
  gobject-introspection,
  glib,
  gtk4,
  libadwaita,
  ibus,
  gsettings-desktop-schemas,
  libxkbcommon,
  wayland,
  xkeyboard-config,
}:

python3Packages.buildPythonApplication {
  pname = "wayswitch";
  version = (lib.importTOML ../../pyproject.toml).project.version;
  pyproject = true;

  # Only product inputs enter the store, including from a working checkout.
  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../pyproject.toml
      ../../README.md
      ../../LICENSE
      (lib.fileset.fromSource (lib.cleanSourceWith {
        src = ../../wayswitch;
        filter = path: type:
          let name = baseNameOf path;
          in !(lib.hasPrefix "." name)
            && !(lib.hasInfix " (conflicted copy " name)
            && (if type == "directory" then name != "__pycache__"
                else type == "regular" && lib.hasSuffix ".py" name);
      }))
      ../../wayswitch/data/LICENSE
      ../../wayswitch/data/ru.words.gz
      ../../wayswitch/data/en.words.gz
      ../../wayswitch/data/ru.ngrams.json.gz
      ../../wayswitch/data/en.ngrams.json.gz
      ../../wayswitch/locale/ru/LC_MESSAGES/wayswitch.po
      ../../wayswitch/locale/ru/LC_MESSAGES/wayswitch.mo
      ../60-wayswitch.rules
      ../wayswitch.service
      ../migrate.py
      ../io.github.Wetoshkin.WaySwitch.Settings.desktop
      ../icons/io.github.Wetoshkin.WaySwitch.svg
      ../icons/io.github.Wetoshkin.WaySwitch-symbolic.svg
      ../icons/io.github.Wetoshkin.WaySwitch-paused-symbolic.svg
      ../kde/io.github.Wetoshkin.wayswitch/metadata.json
      ../kde/io.github.Wetoshkin.wayswitch/contents/ui/main.qml
      (../../extension + "/wayswitch@wetoshkin.github.io/metadata.json")
      (../../extension + "/wayswitch@wetoshkin.github.io/extension.js")
    ];
  };

  build-system = [ python3Packages.setuptools ];
  dependencies = with python3Packages; [ evdev pygobject3 ];
  nativeBuildInputs = [ wrapGAppsHook4 gobject-introspection ];
  buildInputs = [ gtk4 libadwaita ibus gsettings-desktop-schemas ];

  dontWrapGApps = true;
  preFixup = ''
    makeWrapperArgs+=(
      "''${gappsWrapperArgs[@]}"
      --prefix PATH : "${lib.makeBinPath [ glib ]}"
      --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath [ libxkbcommon wayland ]}"
      --set-default XKB_CONFIG_ROOT "${xkeyboard-config}/share/X11/xkb"
    )
  '';

  postInstall = ''
    install -Dm644 packaging/migrate.py "$out/share/wayswitch/migrate.py"
    install -Dm644 packaging/60-wayswitch.rules "$out/lib/udev/rules.d/60-wayswitch.rules"
    install -Dm644 packaging/wayswitch.service "$out/lib/systemd/user/wayswitch.service"
    substituteInPlace "$out/lib/systemd/user/wayswitch.service" \
      --replace-fail /usr/local/bin/wayswitch "$out/bin/wayswitch"
    install -Dm644 packaging/io.github.Wetoshkin.WaySwitch.Settings.desktop \
      "$out/share/applications/io.github.Wetoshkin.WaySwitch.Settings.desktop"
    install -Dm644 packaging/icons/io.github.Wetoshkin.WaySwitch.svg \
      "$out/share/icons/hicolor/scalable/apps/io.github.Wetoshkin.WaySwitch.svg"
    install -Dm644 packaging/icons/io.github.Wetoshkin.WaySwitch-symbolic.svg \
      "$out/share/icons/hicolor/symbolic/apps/io.github.Wetoshkin.WaySwitch-symbolic.svg"
    install -Dm644 packaging/icons/io.github.Wetoshkin.WaySwitch-paused-symbolic.svg \
      "$out/share/icons/hicolor/symbolic/apps/io.github.Wetoshkin.WaySwitch-paused-symbolic.svg"
    install -Dm644 wayswitch/locale/ru/LC_MESSAGES/wayswitch.mo \
      "$out/share/locale/ru/LC_MESSAGES/wayswitch.mo"
    mkdir -p "$out/share/gnome-shell/extensions" "$out/share/plasma/plasmoids"
    cp -r extension/wayswitch@wetoshkin.github.io "$out/share/gnome-shell/extensions/"
    cp -r packaging/kde/io.github.Wetoshkin.wayswitch "$out/share/plasma/plasmoids/"
    install -Dm644 LICENSE "$out/share/licenses/wayswitch/LICENSE"
    install -Dm644 wayswitch/data/LICENSE "$out/share/licenses/wayswitch/DATA-LICENSE"
  '';

  pythonImportsCheck = [ "wayswitch" "evdev" "gi" ];
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    test "$($out/bin/wayswitch version)" = "wayswitch $version"
    export PYTHONPATH="$out/${python3Packages.python.sitePackages}:$PYTHONPATH"
    export XDG_CACHE_HOME="$TMPDIR/wayswitch-check-cache"
    mkdir -p "$XDG_CACHE_HOME"
    export LD_LIBRARY_PATH="${lib.makeLibraryPath [ libxkbcommon wayland ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export XKB_CONFIG_ROOT="${xkeyboard-config}/share/X11/xkb"
    python - <<'PY'
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("IBus", "1.0")
    from gi.repository import Adw, Gtk, IBus
    from wayswitch.keymap import LayoutSpec, XkbKeymap
    keymap = XkbKeymap.from_names([LayoutSpec("us"), LayoutSpec("ru")], [])
    assert len(keymap.specs) == 2
    PY
    mkdir "$TMPDIR/wayswitch-check-schemas"
    cat > "$TMPDIR/wayswitch-check-schemas/wayswitch-check.gschema.xml" <<'XML'
    <schemalist>
      <schema id="org.gnome.desktop.interface" path="/org/gnome/desktop/interface/">
        <key name="toolkit-accessibility" type="b"><default>true</default></key>
      </schema>
    </schemalist>
    XML
    ${glib.dev}/bin/glib-compile-schemas "$TMPDIR/wayswitch-check-schemas"
    env PATH=/nonexistent GSETTINGS_BACKEND=memory \
      GSETTINGS_SCHEMA_DIR="$TMPDIR/wayswitch-check-schemas" \
      "$out/bin/wayswitch" doctor > "$TMPDIR/wayswitch-doctor-check" || true
    grep -Fx '✓ Распознавание программ и полей пароля (настройка GTK / GNOME)' \
      "$TMPDIR/wayswitch-doctor-check"
    runHook postInstallCheck
  '';

  meta = {
    description = "Automatic Russian/English keyboard layout correction on Wayland";
    homepage = "https://github.com/Wetoshkin/WaySwitch";
    license = with lib.licenses; [ mit cc-by-sa-40 ];
    platforms = lib.platforms.linux;
    mainProgram = "wayswitch";
  };
}
