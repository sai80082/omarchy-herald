// Presentation logic for the bar widget and panel.
//   node --test tests/model.test.js

const test = require("node:test")
const assert = require("node:assert")
const M = require("../Model.js")

const MINUTE = 60000
const now = Date.UTC(2026, 8, 16, 12, 0, 0)
const secondsAgo = (mins) => (now - mins * MINUTE) / 1000

test("a long code is grouped in threes, a short one is left alone", () => {
  assert.equal(M.formatCode("281940"), "281 940")
  assert.equal(M.formatCode("4471"), "4471")
  assert.equal(M.formatCode(""), "")
})

test("an OTP row leads with the code, a transaction with the money", () => {
  assert.equal(M.primaryLine({ category: "otp", code: "281940", headline: "Google code" }), "281 940")
  assert.equal(
    M.primaryLine({ category: "transaction", amount: "₹1,249.00", merchant: "Blue Tokai" }),
    "₹1,249.00  Blue Tokai")
  assert.equal(M.primaryLine({ category: "security", headline: "New sign-in" }), "New sign-in")
})

test("a transaction with no amount still says who it was", () => {
  assert.equal(M.primaryLine({ category: "transaction", amount: "", merchant: "Blue Tokai" }), "Blue Tokai")
  assert.equal(
    M.primaryLine({ category: "transaction", amount: "", merchant: "", headline: "Card charged" }),
    "Card charged")
})

test("the secondary line never repeats the primary one", () => {
  const row = { category: "security", title: "GitHub", headline: "New sign-in", detail: "New sign-in" }
  assert.equal(M.secondaryLine(row), "GitHub")
})

test("relative time gets vaguer as it gets older, then gives up", () => {
  assert.equal(M.relativeTime(secondsAgo(0.2), now), "now")
  assert.equal(M.relativeTime(secondsAgo(40), now), "40m")
  assert.equal(M.relativeTime(secondsAgo(200), now), "3h")
  assert.equal(M.relativeTime(secondsAgo(60 * 24 * 3), now), "3d")
  assert.equal(M.relativeTime(secondsAgo(60 * 24 * 40), now), "")
  assert.equal(M.relativeTime(0, now), "")
})

test("a code's remaining life counts down and floors at zero", () => {
  const live = { category: "otp", expires_minutes: 10, receivedAt: secondsAgo(2) }
  assert.equal(M.codeMinutesLeft(live, now), 8)

  const dead = { category: "otp", expires_minutes: 5, receivedAt: secondsAgo(90) }
  assert.equal(M.codeMinutesLeft(dead, now), 0)

  // -1 is "the sender never said", which the UI must not render as expired.
  assert.equal(M.codeMinutesLeft({ category: "otp", expires_minutes: 0, receivedAt: secondsAgo(1) }, now), -1)
  assert.equal(M.codeMinutesLeft({ category: "transaction", expires_minutes: 9 }, now), -1)
})

test("the badge counts unread, and 'actionable' excludes the quiet categories", () => {
  const rows = [
    { category: "otp", seen: false },
    { category: "transaction", seen: false },
    { category: "delivery", seen: false },
    { category: "security", seen: true },
  ]
  assert.equal(M.badgeCount(rows, "actionable"), 2)
  assert.equal(M.badgeCount(rows, "all"), 3)
  assert.equal(M.badgeCount(rows, "off"), 0)
})

test("the badge stops counting where it stops being readable", () => {
  assert.equal(M.badgeText(0), "")
  assert.equal(M.badgeText(9), "9")
  assert.equal(M.badgeText(24), "9+")
})

test("a partial or noisy stdout line is dropped, not thrown", () => {
  assert.equal(M.parseLine('{"t":"event"'), null)
  assert.equal(M.parseLine("Traceback (most recent call last):"), null)
  assert.equal(M.parseLine(""), null)
  assert.deepEqual(M.parseLine('{"t":"log","msg":"hi"}'), { t: "log", msg: "hi" })
})

test("merging is newest-first, de-duplicated, and capped", () => {
  const existing = [{ id: "a", receivedAt: 100 }, { id: "b", receivedAt: 200 }]
  const merged = M.mergeEvents(existing, [{ id: "c", receivedAt: 300 }, { id: "a", receivedAt: 100 }], 10)
  assert.deepEqual(merged.map((r) => r.id), ["c", "b", "a"])
  assert.equal(M.mergeEvents(existing, [{ id: "c", receivedAt: 300 }], 2).length, 2)
})

test("every category and state has its own mark", () => {
  const cats = ["otp", "transaction", "security", "delivery", "calendar", "other"]
  const glyphs = new Set(cats.map(M.categoryGlyph))
  assert.equal(glyphs.size, cats.length, "categories must not share a glyph")
  assert.equal(M.categoryGlyph("nonsense"), M.categoryGlyph("other"))
  assert.equal(M.stateLabel("watching"), "Watching")
  assert.equal(M.stateLabel("nonsense"), "Unknown")
})
