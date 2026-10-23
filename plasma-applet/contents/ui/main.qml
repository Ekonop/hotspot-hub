import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root
    width: Kirigami.Units.gridUnit * 5
    height: Kirigami.Units.gridUnit * 5

    Plasmoid.icon: "network-wireless-hotspot"

    // Tooltip showing hotspot status
    toolTipMainText: "Hotspot Hub"
    toolTipSubText: "Click to open controls"

    // Compact representation (tray icon)
    compactRepresentation: Item {
        Kirigami.Icon {
            anchors.centerIn: parent
            source: "network-wireless-hotspot"
            width: Kirigami.Units.iconSizes.medium
            height: Kirigami.Units.iconSizes.medium
        }
    }

    // Full representation (popup)
    fullRepresentation: Column {
        spacing: Kirigami.Units.smallSpacing
        Kirigami.Heading { text: "Hotspot Hub" }
        // TODO: bind to D-Bus service org.hotspothub.HotspotHub
        // for live status and on/off control.
    }
}
