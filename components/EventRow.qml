import QtQuick
import qs.Commons
import qs.Ui
import "../Model.js" as Model

// One classified message in the panel list.
//
// Layout follows the panel convention: a category mark, two lines of text
// where the first is the thing you came for and the second is the context,
// then the age, then whatever action the row supports. Colour is the only
// thing separating a security alert from a delivery notice, so it is spent
// carefully — urgent for alerts, accent for a code that is still live, and
// plain foreground for everything else.
//
// `foreground` and `accent` are inherited from CursorSurface, which also owns
// the row's hover/cursor paint. Per the CursorSurface contract this row never
// reads containsMouse for its own colours: hover reports up to the panel,
// which owns the one cursor on screen and hands `hasCursor` back down.
CursorSurface {
  id: row

  required property var entry
  property color urgentColor: Color.urgent
  property bool justCopied: false
  property string fontFamily: Style.font.family
  property double now: Date.now()

  signal activated()
  signal hoverChanged(bool isHovered)

  readonly property string category: String(entry.category || "other")
  readonly property bool hasCode: String(entry.code || "") !== ""
  readonly property int minutesLeft: Model.codeMinutesLeft(entry, row.now)
  readonly property bool expired: minutesLeft === 0
  readonly property bool unseen: entry.seen !== true

  readonly property color markColor: {
    if (category === "security") return urgentColor
    if (category === "otp") return expired ? Qt.darker(foreground, 1.6) : accent
    if (category === "transaction") return accent
    return foreground
  }

  implicitHeight: Math.max(Style.space(42), body.implicitHeight + Style.spacing.md * 2)

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    acceptedButtons: Qt.LeftButton
    onEntered: row.hoverChanged(true)
    onExited: row.hoverChanged(false)
    onClicked: row.activated()
  }

  Row {
    id: body
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    anchors.leftMargin: Style.spacing.rowPaddingX
    anchors.rightMargin: Style.spacing.rowPaddingX
    spacing: Style.spacing.controlGap

    OpticalGlyph {
      width: Style.space(20)
      height: Style.space(20)
      anchors.verticalCenter: parent.verticalCenter
      text: Model.categoryGlyph(row.category)
      fontFamily: row.fontFamily
      fontSize: Style.font.iconLarge
      color: row.markColor
    }

    Column {
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(Style.space(80), body.width - body.spacing * 3 - Style.space(20)
                      - age.implicitWidth - copyButton.width)
      spacing: Style.spacing.xxs

      Text {
        textFormat: Text.PlainText
        width: parent.width
        elide: Text.ElideRight
        text: Model.primaryLine(row.entry)
        color: row.expired ? Qt.darker(row.foreground, 1.5) : row.foreground
        font.family: row.fontFamily
        font.pixelSize: row.category === "otp" ? Style.font.subtitle : Style.font.body
        font.bold: row.unseen || row.category === "otp"
        // A code is read character by character off the screen and typed
        // somewhere else; the extra tracking is what makes that possible at
        // this size.
        font.letterSpacing: row.category === "otp" ? 1.2 : 0
        renderType: Text.NativeRendering
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        visible: text !== ""
        elide: Text.ElideRight
        text: {
          var base = Model.secondaryLine(row.entry)
          if (row.category !== "otp" || row.minutesLeft < 0) return base
          var span = row.expired ? "expired" : row.minutesLeft + "m left"
          return base ? base + "  ·  " + span : span
        }
        color: Qt.darker(row.foreground, 1.55)
        font.family: row.fontFamily
        font.pixelSize: Style.font.bodySmall
        renderType: Text.NativeRendering
      }
    }

    Text {
      id: age
      textFormat: Text.PlainText
      anchors.verticalCenter: parent.verticalCenter
      text: Model.relativeTime(row.entry.receivedAt, row.now)
      color: Qt.darker(row.foreground, 1.7)
      font.family: row.fontFamily
      font.pixelSize: Style.font.caption
      renderType: Text.NativeRendering
    }

    PanelActionButton {
      id: copyButton
      anchors.verticalCenter: parent.verticalCenter
      visible: row.hasCode
      // Kept in the layout when hidden so rows without a code line their text
      // up with rows that have one, instead of each row finding its own edge.
      width: row.hasCode ? implicitWidth : 0
      iconText: row.justCopied ? "\u{f012c}" : "\u{f018f}"   // check : content-copy
      tooltipText: row.justCopied ? "Copied" : "Copy code"
      foreground: row.justCopied ? row.accent : row.foreground
      hoverColor: row.accent
      fontFamily: row.fontFamily
      onClicked: row.activated()
    }
  }

  // A small notch on the leading edge, only while the row is unread. It reads
  // at a glance without spending a colour or a column on it.
  Rectangle {
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    width: Style.space(2)
    height: parent.height * 0.5
    radius: width
    visible: row.unseen
    color: row.markColor
  }
}
