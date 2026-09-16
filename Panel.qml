import QtQuick
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "components"

// Herald's panel: what the mailbox has turned up, newest first, with the live
// code promoted to the top.
//
// The hero is the whole argument for this panel existing. When the most recent
// event is a one-time code that has not run out, the code IS the panel — set
// at display size, one click from the clipboard, the way the clock panel gives
// its whole top to today's date. Any other time the hero falls back to saying
// what the watcher is doing, which is the next most useful thing a mail widget
// can tell you.
//
// BarWidget.qml owns the bar glyph and hands this panel the button to anchor
// against.
Panel {
  id: root
  moduleName: "io.github.sai80082.herald"
  ipcTarget: "io.github.sai80082.herald"
  manageIpc: false

  property var anchorItem: null

  // The bar tracks the widget mounted in its slot — BarWidget.qml — not this
  // nested panel, so everything the bar identifies a panel by has to be that
  // widget: the popout coordinator compares against `slot.activeItem`, and
  // switchPanelFrom looks the slot up the same way.
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("io.github.sai80082.herald") : null

  // Guarded so the panel renders before the bar is injected — the bar-widget
  // contract instantiates it bare.
  readonly property color contentForeground: bar ? bar.foreground : Color.foreground
  readonly property string contentFontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color urgentColor: bar ? bar.urgent : Color.urgent

  property double nowMs: Date.now()
  property int cursorIndex: -1

  readonly property int maxRows: Math.max(3, Math.min(40, Number(setting("historyRows", 12))))
  readonly property var rows: {
    var all = service ? service.taggedEvents : []
    return all.slice(0, root.maxRows)
  }

  readonly property string watchState: service ? service.watchState : "starting"
  readonly property string watchDetail: service ? service.watchDetail : ""
  readonly property string account: service ? service.account : ""

  // The hero's code. Only a code that is both the newest thing to arrive and
  // still valid earns the top of the panel; anything else and the hero would
  // be showing a stale number with more confidence than it deserves.
  readonly property var heroEvent: {
    if (!rows.length) return null
    var first = rows[0]
    if (first.category !== "otp" || !first.code) return null
    var left = Model.codeMinutesLeft(first, root.nowMs)
    if (left === 0) return null
    // An unstated validity is treated as ten minutes. Codes that do not say
    // are almost always short-lived, and a hero that never expires would sit
    // there offering yesterday's number.
    if (left < 0 && (root.nowMs - Number(first.receivedAt) * 1000) > 600000) return null
    return first
  }

  // ---- lifecycle

  function open() {
    root.nowMs = Date.now()
    root.cursorIndex = -1
    root.controller.show()
    // Set after showing: showing hands the popout coordinator over, which
    // closes whichever panel was open, and that close clears the shared flag.
    Qt.callLater(function () {
      if (root.opened) root.setCenterHoverRevealSuppressed(true)
    })
  }

  function close() {
    root.setCenterHoverRevealSuppressed(false)
    // Opening the panel is the acknowledgement; marking on close rather than
    // on open means the rows you just read still show as unread while you
    // read them.
    if (root.service) root.service.markAllSeen()
    root.controller.hide()
  }

  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  // Summoning by hotkey moves no pointer, so a hover the bar was still holding
  // must not keep the center indicators revealed behind the panel.
  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && "centerHoverRevealSuppressed" in root.bar)
      root.bar.centerHoverRevealSuppressed = value
  }

  // ---- actions

  function activate(index) {
    if (index < 0) { copyHero(); return }
    var row = rows[index]
    if (!row) return
    if (row.code && root.service) root.service.copyCode(row.id)
  }

  function copyHero() {
    if (root.heroEvent && root.service) root.service.copyCode(root.heroEvent.id)
  }

  function moveCursor(delta) {
    var lowest = root.heroEvent ? -1 : 0
    if (!rows.length) { root.cursorIndex = lowest; return }
    var next = root.cursorIndex + delta
    root.cursorIndex = Math.max(lowest, Math.min(rows.length - 1, next))
  }

  function reconnect() { if (root.service) root.service.restartWatcher() }

  function runSetup() {
    if (!root.bar || !root.service) return
    root.bar.run("omarchy-launch-floating-terminal-with-presentation "
                 + root.service.pluginDir + "/bin/herald-setup")
  }

  Timer {
    // Ages and code countdowns only move while someone is looking at them.
    interval: 20000
    running: root.opened
    repeat: true
    triggeredOnStart: true
    onTriggered: root.nowMs = Date.now()
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function (dx, dy) { if (dy !== 0) root.moveCursor(dy) }
      onActivateRequested: root.activate(root.cursorIndex)
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        if (t === "r" || t === "R") root.reconnect()
        else if (t === "c" || t === "C") root.copyHero()
        else if (t === "m" || t === "M") { if (root.service) root.service.markAllSeen() }
      }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          width: scroll.width
          spacing: Style.spacing.md

          // ---- Hero ------------------------------------------------------

          Item {
            width: parent.width
            height: heroStack.implicitHeight + Style.spacing.lg

            Column {
              id: heroStack
              anchors.horizontalCenter: parent.horizontalCenter
              anchors.top: parent.top
              anchors.topMargin: Style.spacing.sm
              spacing: Style.spacing.xs

              // Live code: the panel's reason to exist.
              Row {
                visible: root.heroEvent !== null
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.spacing.xxl

                OpticalGlyph {
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(34)
                  height: Style.space(34)
                  text: "\u{f0bc4}"
                  fontFamily: root.contentFontFamily
                  fontSize: Style.space(32)
                  color: heroMouse.containsMouse
                    ? Style.hoverStateColor(root.contentForeground, Color.accent)
                    : Color.accent
                }

                Text {
                  id: heroCode
                  textFormat: Text.PlainText
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.heroEvent ? Model.formatCode(root.heroEvent.code) : ""
                  color: heroMouse.containsMouse
                    ? Style.hoverStateColor(root.contentForeground, Color.accent)
                    : root.contentForeground
                  font.family: root.contentFontFamily
                  font.pixelSize: Style.font.displayLarge
                  font.bold: true
                  font.letterSpacing: 3
                  renderType: Text.NativeRendering
                }
              }

              Text {
                textFormat: Text.PlainText
                visible: root.heroEvent !== null
                anchors.horizontalCenter: parent.horizontalCenter
                text: {
                  if (!root.heroEvent) return ""
                  var bits = [String(root.heroEvent.title || "")]
                  var left = Model.codeMinutesLeft(root.heroEvent, root.nowMs)
                  if (left > 0) bits.push("expires in " + left + "m")
                  bits.push("click to copy")
                  return bits.filter(function (b) { return b !== "" }).join("  ·  ")
                }
                color: Qt.darker(root.contentForeground, 1.5)
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.bodySmall
                renderType: Text.NativeRendering
              }

              // No live code: say what the watcher is doing instead.
              Row {
                visible: root.heroEvent === null
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.spacing.xl

                OpticalGlyph {
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(26)
                  height: Style.space(26)
                  text: Model.stateGlyph(root.watchState)
                  fontFamily: root.contentFontFamily
                  fontSize: Style.space(24)
                  color: root.watchState === "watching"
                    ? root.contentForeground : root.urgentColor
                }

                Column {
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.spacing.xxs

                  Text {
                    textFormat: Text.PlainText
                    text: Model.stateLabel(root.watchState)
                    color: root.contentForeground
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.heading
                    font.bold: true
                    renderType: Text.NativeRendering
                  }

                  Text {
                    textFormat: Text.PlainText
                    visible: text !== ""
                    width: Math.min(implicitWidth, Style.space(300))
                    elide: Text.ElideRight
                    text: root.watchState === "watching"
                      ? root.account
                      : (root.watchDetail || root.account)
                    color: Qt.darker(root.contentForeground, 1.5)
                    font.family: root.contentFontFamily
                    font.pixelSize: Style.font.bodySmall
                    renderType: Text.NativeRendering
                  }
                }
              }
            }

            MouseArea {
              id: heroMouse
              x: heroStack.x
              y: heroStack.y
              width: heroStack.width
              height: heroStack.height
              enabled: root.heroEvent !== null
              hoverEnabled: enabled
              cursorShape: Qt.PointingHandCursor
              onClicked: root.copyHero()

              PanelToolTip {
                visible: heroMouse.containsMouse
                text: "Copy to clipboard"
                fontFamily: root.contentFontFamily
              }
            }
          }

          PanelSeparator { foreground: root.contentForeground }

          // ---- Recent ----------------------------------------------------

          PanelSectionHeader {
            x: Style.spacing.rowPaddingX
            text: root.rows.length ? "RECENT" : "NOTHING YET"
            foreground: root.contentForeground
            fontFamily: root.contentFontFamily
          }

          Text {
            textFormat: Text.PlainText
            visible: root.rows.length === 0
            x: Style.spacing.rowPaddingX
            width: parent.width - Style.spacing.rowPaddingX * 2
            wrapMode: Text.WordWrap
            text: root.watchState === "unconfigured"
              ? "No mailbox configured yet. Run the setup below, then Herald will announce codes and transactions as they arrive."
              : "Herald is watching. Codes, transactions, and security alerts will appear here as mail arrives."
            color: Qt.darker(root.contentForeground, 1.5)
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            renderType: Text.NativeRendering
          }

          Repeater {
            model: root.rows

            EventRow {
              required property int index
              required property var modelData

              width: column.width
              entry: modelData
              hasCursor: root.cursorIndex === index
              foreground: root.contentForeground
              accent: Color.accent
              urgentColor: root.urgentColor
              fontFamily: root.contentFontFamily
              now: root.nowMs
              justCopied: root.service ? root.service.lastCopiedId === modelData.id : false

              onHoverChanged: function (isHovered) {
                // The panel owns the single cursor on screen; the row only
                // reports. Leaving resets to the hero rather than to nothing,
                // so the keyboard always has somewhere to start from.
                root.cursorIndex = isHovered ? index : -1
              }
              onActivated: root.activate(index)
            }
          }

          PanelSeparator { foreground: root.contentForeground }

          // ---- Footer ----------------------------------------------------

          Item {
            width: parent.width
            height: footer.implicitHeight + Style.spacing.sm

            Row {
              id: footer
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.spacing.rowPaddingX
              anchors.rightMargin: Style.spacing.rowPaddingX
              spacing: Style.spacing.controlGap

              Text {
                textFormat: Text.PlainText
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - actions.implicitWidth - parent.spacing
                elide: Text.ElideRight
                text: {
                  if (root.watchState === "watching")
                    return root.rows.length + (root.rows.length === 1 ? " event" : " events")
                      + "  ·  r reconnect  ·  m mark read"
                  return root.watchDetail || Model.stateLabel(root.watchState)
                }
                color: Qt.darker(root.contentForeground, 1.7)
                font.family: root.contentFontFamily
                font.pixelSize: Style.font.caption
                renderType: Text.NativeRendering
              }

              Row {
                id: actions
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.spacing.sm

                PanelActionButton {
                  visible: root.watchState === "unconfigured"
                  width: visible ? implicitWidth : 0
                  iconText: "\u{f0493}"   // nf-md-cog
                  tooltipText: "Run setup"
                  foreground: root.contentForeground
                  hoverColor: Color.accent
                  fontFamily: root.contentFontFamily
                  onClicked: root.runSetup()
                }

                PanelActionButton {
                  iconText: "\u{f0450}"   // nf-md-refresh
                  tooltipText: "Reconnect the watcher"
                  foreground: root.contentForeground
                  hoverColor: Color.accent
                  fontFamily: root.contentFontFamily
                  onClicked: root.reconnect()
                }

                PanelActionButton {
                  iconText: "\u{f012c}"   // nf-md-check
                  tooltipText: "Mark everything read"
                  foreground: root.contentForeground
                  hoverColor: Color.accent
                  fontFamily: root.contentFontFamily
                  onClicked: if (root.service) root.service.markAllSeen()
                }
              }
            }
          }
        }
      }
    }
  }
}
