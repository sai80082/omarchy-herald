import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// The bar half of Herald: one glyph that says whether the mailbox is being
// watched, and whether anything is waiting to be read off it.
//
// The glyph changes rather than growing a badge, because the bar is 26px tall
// and a count painted into that space is decoration, not information. The
// number rides alongside it on horizontal bars where there is room for it, and
// the alert glyph carries the same news on vertical bars where there is not.
//
// Panel.qml is loaded from here and handed this button to anchor against —
// the same nesting the built-in clock uses, so `omarchy-shell shell summon
// io.github.sai80082.herald` routes through the bar's normal panel machinery.
BarWidget {
  id: root
  moduleName: "io.github.sai80082.herald"

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("io.github.sai80082.herald") : null

  readonly property string badgeMode: setting("badge", "actionable")
  readonly property bool hideWhenQuiet: setting("hideWhenQuiet", false) === true

  readonly property string watchState: service ? service.watchState : "starting"
  readonly property int unseen: service ? service.unseen(badgeMode) : 0
  readonly property bool degraded: watchState === "error" || watchState === "unconfigured"
                                   || watchState === "stopped"

  readonly property string glyph: {
    if (degraded) return Model.stateGlyph(watchState)
    if (unseen > 0) return "\u{f06cf}"   // nf-md-email_alert
    return "\u{f01ee}"                   // nf-md-email
  }

  readonly property string countText: root.vertical || badgeMode === "off"
    ? "" : Model.badgeText(unseen)

  readonly property string tooltip: {
    var lines = [Model.stateLabel(watchState)]
    if (service && service.account) lines.push(service.account)
    if (unseen > 0) lines.push(unseen + (unseen === 1 ? " unread event" : " unread events"))
    if (service && service.watchDetail && degraded) lines.push(service.watchDetail)
    return lines.join("  ·  ")
  }

  // A quiet mailbox can take the widget off the bar entirely, but never while
  // something is wrong — a watcher that has lost its connection has to stay
  // visible or the silence reads as "no mail" instead of "not looking".
  visible: !hideWhenQuiet || unseen > 0 || degraded

  // ---- Panel. Shape contract for shell summon/hide/toggle routing:
  //      Bar.findPanelWidget requires open/close/opened on the widget root.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  // The dot the bar paints under an open panel takes the width of the mark
  // this widget actually paints, not the slot it sits in.
  readonly property real openPanelIndicatorWidth: button.width
  readonly property real openPanelIndicatorHeight: Math.max(Style.space(10),
    Math.round(Style.bar.iconSlot * 0.55))

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "io.github.sai80082.herald"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    active: root.unseen > 0 || root.watchState === "error"
    dimmed: root.watchState === "unconfigured" || root.watchState === "connecting"
    tooltipText: root.tooltip
    fixedWidth: root.vertical ? -1 : Style.bar.iconSlot + content.extraWidth
    fixedHeight: root.vertical ? Style.bar.iconSlot : -1

    onPressed: function (b) {
      if (b === Qt.RightButton) { if (root.service) root.service.restartWatcher() }
      else if (b === Qt.MiddleButton) { if (root.service) root.service.markAllSeen() }
      else root.togglePanel()
    }

    Row {
      id: content
      anchors.centerIn: parent
      spacing: root.countText !== "" ? Style.space(3) : 0

      readonly property real extraWidth: root.countText === ""
        ? 0 : count.implicitWidth + spacing

      OpticalGlyph {
        id: mark
        width: Style.bar.iconCanvas
        height: Style.bar.iconCanvas
        anchors.verticalCenter: parent.verticalCenter
        text: root.glyph
        fontFamily: button.fontFamily
        fontSize: Style.bar.iconFont
        color: button.active && button.useActiveColor ? button.activeColor : button.foreground
      }

      Text {
        id: count
        textFormat: Text.PlainText
        anchors.verticalCenter: parent.verticalCenter
        visible: root.countText !== ""
        text: root.countText
        color: mark.color
        font.family: button.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        renderType: Text.NativeRendering
      }
    }
  }
}
