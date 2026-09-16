// Pure presentation logic for Herald's bar widget and panel.
//
// Nothing in here touches Qt or the daemon, so `node --test tests/model.test.js`
// covers the parts most likely to be wrong — time phrasing, badge arithmetic,
// and how a half-filled verdict from a small model is turned into two lines of
// text — without a running shell.

// nf-md glyphs, matching the set the daemon sends to the notification server so
// a toast and its panel row carry the same mark.
var CATEGORY_GLYPHS = {
  otp: "\u{f0bc4}",          // shield-key
  transaction: "\u{f019b}",  // credit-card
  security: "\u{f0ecc}",     // shield-alert
  delivery: "\u{f0788}",     // truck-fast
  calendar: "\u{f00ed}",     // calendar
  other: "\u{f01ee}"         // email
}

var CATEGORY_LABELS = {
  otp: "CODE",
  transaction: "MONEY",
  security: "SECURITY",
  delivery: "DELIVERY",
  calendar: "CALENDAR",
  other: "MAIL"
}

// The categories worth a badge on the bar. Deliveries and calendar invites are
// worth recording and worth a quiet toast, but they are not worth a number
// sitting in the corner of the screen until it is acknowledged.
var ACTIONABLE = ["otp", "transaction", "security"]

var STATE_GLYPHS = {
  watching: "\u{f01ee}",      // email
  connecting: "\u{f04e6}",    // sync
  starting: "\u{f04e6}",
  error: "\u{f0164}",         // cloud-off
  unconfigured: "\u{f0028}",  // alert-circle
  stopped: "\u{f0164}"
}

var STATE_LABELS = {
  watching: "Watching",
  connecting: "Connecting",
  starting: "Starting",
  error: "Disconnected",
  unconfigured: "Not set up",
  stopped: "Stopped"
}

function categoryGlyph(category) {
  return CATEGORY_GLYPHS[category] || CATEGORY_GLYPHS.other
}

function categoryLabel(category) {
  return CATEGORY_LABELS[category] || CATEGORY_LABELS.other
}

function stateGlyph(state) {
  return STATE_GLYPHS[state] || STATE_GLYPHS.error
}

function stateLabel(state) {
  return STATE_LABELS[state] || "Unknown"
}

function isActionable(category) {
  return ACTIONABLE.indexOf(String(category)) !== -1
}

// A code is easier to read off the screen and type in three-digit groups, but
// only once it is long enough for grouping to help. Four digits read as one
// chunk already, and splitting them makes them slower to scan, not faster.
function formatCode(code) {
  var raw = String(code || "")
  if (raw.length <= 4) return raw
  var out = []
  for (var i = 0; i < raw.length; i += 3) out.push(raw.substr(i, 3))
  return out.join(" ")
}

// The row's bold line. An OTP leads with its code because that is the only
// thing anyone opened the panel to read; a transaction leads with the amount
// for the same reason.
function primaryLine(event) {
  if (!event) return ""
  if (event.category === "otp" && event.code) return formatCode(event.code)
  if (event.category === "transaction") {
    var amount = String(event.amount || "")
    var merchant = String(event.merchant || "")
    if (amount && merchant) return amount + "  " + merchant
    if (amount) return amount
    if (merchant) return merchant
  }
  return String(event.headline || event.subject || "Message")
}

// The quiet line under it: who it came from, and whatever context the model
// found that is not already in the primary line.
function secondaryLine(event) {
  if (!event) return ""
  var parts = []
  var title = String(event.title || "")
  if (title) parts.push(title)

  var detail = String(event.detail || "")
  if (!detail && event.category !== "otp") detail = String(event.subject || "")
  if (detail && detail !== primaryLine(event)) parts.push(detail)

  if (event.category === "otp" && event.account_hint) parts.push(String(event.account_hint))
  if (event.category === "transaction" && event.account_hint) parts.push(String(event.account_hint))

  return parts.join("  ·  ")
}

// Relative time, in the shortest phrasing that is still honest. Anything past a
// week is a date: "8d" tells you less than "3 Feb" and takes the same space.
function relativeTime(seconds, nowMs) {
  var then = Number(seconds) * 1000
  if (!isFinite(then) || then <= 0) return ""
  var delta = Math.max(0, (nowMs || Date.now()) - then)

  var mins = Math.floor(delta / 60000)
  if (mins < 1) return "now"
  if (mins < 60) return mins + "m"
  var hours = Math.floor(mins / 60)
  if (hours < 24) return hours + "h"
  var days = Math.floor(hours / 24)
  if (days < 7) return days + "d"
  return ""
}

// Minutes left on a code, from the validity the sender stated. Returns -1 when
// the message never said, and 0 once it has run out.
function codeMinutesLeft(event, nowMs) {
  if (!event || event.category !== "otp") return -1
  var span = Number(event.expires_minutes || 0)
  if (!isFinite(span) || span <= 0) return -1
  var elapsed = ((nowMs || Date.now()) - Number(event.receivedAt) * 1000) / 60000
  return Math.max(0, Math.round(span - elapsed))
}

// Badge arithmetic. `mode` is the widget's `badge` setting: the whole point of
// "actionable" is that a delivery notice should not keep a number lit.
function badgeCount(events, mode) {
  if (mode === "off") return 0
  var count = 0
  for (var i = 0; i < events.length; i++) {
    var row = events[i]
    if (!row || row.seen) continue
    if (mode === "all" || isActionable(row.category)) count++
  }
  return count
}

// Badges stop being countable well before they stop being wide.
function badgeText(count) {
  if (count <= 0) return ""
  return count > 9 ? "9+" : String(count)
}

// One line of the daemon's stdout. A partial write or a stray print from a
// dependency must not take the parser down, so anything unparseable is
// reported as null and dropped by the caller.
function parseLine(line) {
  var text = String(line || "").trim()
  if (!text || text.charAt(0) !== "{") return null
  try {
    var parsed = JSON.parse(text)
    return parsed && typeof parsed === "object" ? parsed : null
  } catch (e) {
    return null
  }
}

// Newest first, de-duplicated by id, capped. The daemon already dedupes by
// UID, but a restart replays history into a model that may still hold it.
function mergeEvents(existing, incoming, limit) {
  var out = []
  var seen = {}
  var all = incoming.concat(existing)
  for (var i = 0; i < all.length; i++) {
    var row = all[i]
    if (!row || !row.id || seen[row.id]) continue
    seen[row.id] = true
    out.push(row)
  }
  out.sort(function (a, b) { return Number(b.receivedAt || 0) - Number(a.receivedAt || 0) })
  return out.slice(0, limit || 200)
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    categoryGlyph: categoryGlyph,
    categoryLabel: categoryLabel,
    stateGlyph: stateGlyph,
    stateLabel: stateLabel,
    isActionable: isActionable,
    formatCode: formatCode,
    primaryLine: primaryLine,
    secondaryLine: secondaryLine,
    relativeTime: relativeTime,
    codeMinutesLeft: codeMinutesLeft,
    badgeCount: badgeCount,
    badgeText: badgeText,
    parseLine: parseLine,
    mergeEvents: mergeEvents
  }
}
