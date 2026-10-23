// Виджет WaySwitch для системного лотка KDE Plasma 6.
//
// Состояние берёт у службы командой `wayswitch status --json` (раз в две
// секунды), управляет командами `wayswitch toggle` / `fix` / `gui`. Так виджет
// не зависит от D-Bus-модулей QML и работает в любой Plasma 6.
// Щелчок — открыть, средний щелчок — пауза.

import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    // null — служба не отвечает
    property var status: null
    readonly property bool running: status !== null
    readonly property bool paused: running && status.paused === true
    readonly property string layoutName: !running ? "" :
        status.layout === "ru" ? "русская" : status.layout === "latin" ? "английская" : "неизвестна"
    readonly property int fixedTotal: running ? (status.corrections_total || 0) + (status.manual_total || 0) : 0

    readonly property string stateTitle: !running ? "WaySwitch не запущен"
        : paused ? "На паузе"
        : status.active ? "Исправление включено" : "Только вручную"
    readonly property string stateText: !running ? "Слова сейчас не исправляются."
        : paused ? "Слова не исправляются"
        : "Раскладка: " + layoutName

    Plasmoid.icon: (running && !paused) ? "ru.siberia.WaySwitch-symbolic"
                                        : "ru.siberia.WaySwitch-paused-symbolic"
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    toolTipMainText: "WaySwitch"
    toolTipSubText: stateTitle + (running ? "\n" + stateText : "")

    P5Support.DataSource {
        id: shell
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            if (source === root.statusCommand) {
                let parsed = null
                if (data["exit code"] === 0) {
                    try { parsed = JSON.parse(data["stdout"]) } catch (e) { parsed = null }
                }
                root.status = parsed
            }
            disconnectSource(source)
        }
        function run(command) { connectSource(command) }
    }

    readonly property string statusCommand: "wayswitch status --json"

    function refresh() { shell.run(statusCommand) }
    function command(name) {
        shell.run("wayswitch " + name)
        refreshSoon.restart()
    }

    Timer {
        interval: 2000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: root.refresh()
    }
    Timer { id: refreshSoon; interval: 300; onTriggered: root.refresh() }

    compactRepresentation: MouseArea {
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        hoverEnabled: true
        onClicked: mouse => {
            if (mouse.button === Qt.MiddleButton) {
                root.command("toggle")
            } else {
                root.expanded = !root.expanded
            }
        }
        Kirigami.Icon {
            anchors.fill: parent
            source: Plasmoid.icon
            active: parent.containsMouse
        }
    }

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12
        collapseMarginsHint: true

        header: PlasmaExtras.PlasmoidHeading {
            RowLayout {
                anchors.fill: parent
                PlasmaExtras.Heading {
                    Layout.fillWidth: true
                    level: 1
                    text: "WaySwitch"
                }
                PlasmaComponents3.ToolButton {
                    icon.name: "configure"
                    display: PlasmaComponents3.AbstractButton.IconOnly
                    text: "Параметры"
                    onClicked: root.command("gui")
                    PlasmaComponents3.ToolTip.text: text
                    PlasmaComponents3.ToolTip.visible: hovered
                }
            }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.largeSpacing
                Kirigami.Icon {
                    source: "ru.siberia.WaySwitch"
                    Layout.preferredWidth: Kirigami.Units.iconSizes.large
                    Layout.preferredHeight: Kirigami.Units.iconSizes.large
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    PlasmaExtras.Heading {
                        level: 3
                        text: root.stateTitle
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                    PlasmaComponents3.Label {
                        text: root.stateText
                        opacity: 0.7
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                }
                PlasmaComponents3.Switch {
                    visible: root.running
                    checked: root.running && !root.paused
                    onToggled: root.command("toggle")
                    Accessible.name: "Исправление включено"
                }
            }

            PlasmaComponents3.Button {
                Layout.fillWidth: true
                visible: root.running
                icon.name: "tools-check-spelling"
                text: "Исправить последнее слово"
                onClicked: root.command("fix")
            }

            PlasmaComponents3.Button {
                Layout.fillWidth: true
                visible: !root.running
                icon.name: "media-playback-start"
                text: "Запустить"
                onClicked: shell.run("systemctl --user start wayswitch")
            }

            Item { Layout.fillHeight: true }

            PlasmaComponents3.Label {
                Layout.fillWidth: true
                visible: root.running
                opacity: 0.7
                text: "За сеанс: " + (root.running ? (root.status.corrections_total || 0) : 0)
                      + " автоматически, " + (root.running ? (root.status.manual_total || 0) : 0)
                      + " вручную"
            }
        }
    }
}
