import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// Herald's headless half: it owns the mail watcher, holds the events it has
// reported, and stays loaded whether or not the bar widget is on screen.
//
// The watcher itself is a Python process (bin/herald-daemon) that speaks one
// JSON object per line on stdout. Everything about IMAP and the model lives
// there; this file is lifecycle and state. That split is deliberate — the
// daemon can be run in a terminal, or from systemd, and behaves identically.
Item {
  id: service

  // Injected by shell.qml's service loader.
  property var shell: null
  property var manifest: null
  property string omarchyPath: ""

  readonly property string home: Quickshell.env("HOME")
  readonly property string configPath: home + "/.config/omarchy/herald/config.json"
  readonly property string stateDir: home + "/.local/state/omarchy/herald/"
  readonly property string eventsPath: stateDir + "events.jsonl"
  readonly property string seenPath: stateDir + "seen.json"

  // Qt.resolvedUrl beats manifest.__sourceDir here: it is right even when the
  // service is instantiated directly (a qmllint run, a dev harness) with no
  // registry to inject a manifest.
  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    var path = url.indexOf("file://") === 0 ? url.substring(7) : url
    return path.replace(/\/$/, "")
  }

  // ---- state the widget and panel read

  // One of: starting, connecting, watching, error, unconfigured, stopped.
  property string watchState: "starting"
  property string watchDetail: ""
  property string account: ""
  property var events: []
  property double seenBefore: 0
  property string lastError: ""

  // False while the daemon is deliberately parked (no config yet, or the user
  // runs it themselves). Distinguishes "broken" from "not my job".
  property bool managed: true
  property bool configured: false

  readonly property bool healthy: watchState === "watching"

  signal eventArrived(var event)

  function unseen(mode) {
    return Model.badgeCount(taggedEvents, mode)
  }

  // `seen` is derived rather than stored per row: one timestamp survives a
  // restart, needs no reconciliation against a trimmed history file, and
  // cannot drift out of step with the rows it describes.
  readonly property var taggedEvents: {
    var out = []
    for (var i = 0; i < events.length; i++) {
      var row = events[i]
      var copy = {}
      for (var key in row) copy[key] = row[key]
      copy.seen = Number(row.receivedAt || 0) <= service.seenBefore
      out.push(copy)
    }
    return out
  }

  function markAllSeen() {
    var newest = 0
    for (var i = 0; i < events.length; i++)
      newest = Math.max(newest, Number(events[i].receivedAt || 0))
    if (newest <= seenBefore) return
    seenBefore = newest
    seenFile.setText(JSON.stringify({ seenBefore: newest }))
  }

  function eventById(id) {
    for (var i = 0; i < events.length; i++)
      if (events[i].id === id) return events[i]
    return null
  }

  // Briefly non-empty after a successful copy, so a row can acknowledge the
  // click without a toast the user did not ask for.
  property string lastCopiedId: ""

  function copyCode(id) {
    var row = eventById(id)
    if (!row || !row.code) return false
    // The code rides in through the environment rather than argv so it never
    // appears in /proc/<pid>/cmdline, and `env -u` drops it before wl-copy
    // itself is exec'd — wl-copy's clipboard owner outlives this call.
    Quickshell.execDetached({
      command: ["bash", "-c", "printf '%s' \"$HERALD_CLIP\" | env -u HERALD_CLIP wl-copy --sensitive"],
      environment: { "HERALD_CLIP": String(row.code) }
    })
    service.lastCopiedId = String(id)
    copiedTimer.restart()
    return true
  }

  function restartWatcher() {
    restartTimer.stop()
    backoffMs = 3000
    daemon.running = false
    Qt.callLater(function () { daemon.running = service.managed && service.configured })
  }

  // ---- daemon plumbing

  property int backoffMs: 3000
  property bool stopping: false

  function ingest(line) {
    var row = Model.parseLine(line)
    if (!row) return

    if (row.t === "status") {
      service.watchState = String(row.state || "")
      service.watchDetail = String(row.detail || "")
      // The address on its own. `detail` reads "you@example.com · INBOX",
      // which is the right line for a status strip and the wrong one for
      // anywhere the account is named by itself.
      if (row.account) service.account = String(row.account)
      if (row.state === "watching") {
        service.lastError = ""
        service.backoffMs = 3000
      } else if (row.state === "error") {
        service.lastError = String(row.detail || "")
      }
      return
    }

    if (row.t === "log") {
      if (row.level === "error" || row.level === "warn") {
        service.lastError = String(row.msg || "")
        console.warn("herald: " + row.msg)
      }
      return
    }

    if (row.t === "event") {
      service.events = Model.mergeEvents(service.events, [row], 200)
      service.eventArrived(row)
    }
  }

  function loadHistory(text) {
    var lines = String(text || "").split("\n")
    var rows = []
    for (var i = 0; i < lines.length; i++) {
      var row = Model.parseLine(lines[i])
      if (row && row.t === "event") rows.push(row)
    }

    // The file is authoritative: the daemon appends an event before it prints
    // it, so anything the panel has seen is already in there. Replacing rather
    // than accumulating means a trimmed or cleared history actually clears,
    // instead of lingering in memory until the shell restarts.
    //
    // Live rows newer than everything in the file are still kept, so an event
    // that arrives on stdout while this read is in flight is not lost.
    var newest = 0
    for (var j = 0; j < rows.length; j++)
      newest = Math.max(newest, Number(rows[j].receivedAt || 0))

    var pending = []
    for (var k = 0; k < service.events.length; k++)
      if (Number(service.events[k].receivedAt || 0) > newest) pending.push(service.events[k])

    service.events = Model.mergeEvents(rows, pending, 200)
  }

  function loadSeen(text) {
    try {
      var parsed = JSON.parse(String(text || "{}"))
      service.seenBefore = Number(parsed.seenBefore || 0)
    } catch (e) {
      service.seenBefore = 0
    }
  }

  function loadConfig(text) {
    if (!text) {
      service.configured = false
      service.watchState = "unconfigured"
      service.watchDetail = service.configPath
      return
    }
    try {
      var parsed = JSON.parse(text)
      var imap = parsed.imap || {}
      service.configured = !!(imap.host && imap.username)
      service.managed = parsed.managed !== false
      service.account = String(imap.username || "")
      if (!service.configured) {
        service.watchState = "unconfigured"
        service.watchDetail = "IMAP host and username are not set"
      }
    } catch (e) {
      service.configured = false
      service.watchState = "error"
      service.watchDetail = "config.json is not valid JSON"
    }
    // A saved config is the signal to (re)start: editing credentials should
    // reconnect without anyone restarting the shell.
    if (service.configured && service.managed) restartWatcher()
    else daemon.running = false
  }

  Process {
    id: daemon
    running: false
    command: ["python3", service.pluginDir + "/bin/herald-daemon", "--config", service.configPath]

    stdout: SplitParser {
      onRead: function (line) { service.ingest(line) }
    }
    // The daemon keeps its own output on stdout; anything on stderr is a
    // Python traceback or an unhandled warning, and belongs in the shell log
    // where `qs log` will show it next to the failure it explains.
    stderr: SplitParser {
      onRead: function (line) {
        if (String(line).trim()) console.warn("herald-daemon: " + line)
      }
    }

    // Exit codes the daemon uses for failures a retry cannot fix: 3 is a
    // config that cannot work, 4 is a login the server kept refusing. Both
    // park the watcher rather than restart it — the config file is watched,
    // so saving a fix starts it again, and the panel's refresh button and
    // `omarchy-shell herald restart` are the manual way back.
    readonly property int exitUnconfigured: 3
    readonly property int exitAuth: 4

    onExited: function (exitCode) {
      if (service.stopping || !service.managed || !service.configured) return

      if (exitCode === daemon.exitUnconfigured) {
        service.watchState = "unconfigured"
        return
      }
      if (exitCode === daemon.exitAuth) {
        service.watchState = "error"
        if (!service.watchDetail) service.watchDetail = "login rejected"
        return
      }

      service.watchState = "error"
      service.watchDetail = "watcher exited (" + exitCode + ")"
      restartTimer.interval = service.backoffMs
      restartTimer.restart()
      // Two minutes is the ceiling: past that a mailbox is unwatched long
      // enough that the next code to arrive is missed entirely.
      service.backoffMs = Math.min(120000, service.backoffMs * 2)
    }
  }

  Timer {
    id: restartTimer
    repeat: false
    onTriggered: daemon.running = service.managed && service.configured
  }

  Timer {
    id: copiedTimer
    interval: 2000
    repeat: false
    onTriggered: service.lastCopiedId = ""
  }

  FileView {
    id: configFile
    path: service.configPath
    watchChanges: true
    printErrors: false
    onLoaded: service.loadConfig(text())
    onFileChanged: reload()
    onLoadFailed: service.loadConfig("")
  }

  // Watched, not just read once: a daemon the user runs themselves (managed:
  // false, or a systemd unit) still fills the panel through this file.
  FileView {
    id: eventsFile
    path: service.eventsPath
    watchChanges: true
    printErrors: false
    onLoaded: {
      historyRetry.running = false
      service.loadHistory(text())
    }
    onFileChanged: reload()
    // Before the first event there is no file to watch, and a watch placed on
    // a path that does not exist never fires when it appears. Poll until it
    // does, then stop — this costs one stat every ten seconds, exactly once
    // per install.
    onLoadFailed: historyRetry.running = true
  }

  Timer {
    id: historyRetry
    interval: 10000
    repeat: true
    running: false
    onTriggered: eventsFile.reload()
  }

  FileView {
    id: seenFile
    path: service.seenPath
    watchChanges: false
    atomicWrites: true
    printErrors: false
    onLoaded: service.loadSeen(text())
    onLoadFailed: service.loadSeen("")
  }

  IpcHandler {
    target: "herald"

    function status(): string {
      return JSON.stringify({
        state: service.watchState,
        detail: service.watchDetail,
        account: service.account,
        events: service.events.length,
        unseen: service.unseen("actionable"),
        managed: service.managed,
        configured: service.configured
      })
    }

    function restart(): void { service.restartWatcher() }
    function markSeen(): void { service.markAllSeen() }
    function copy(id: string): string { return service.copyCode(id) ? "ok" : "unknown" }
  }

  Component.onDestruction: {
    service.stopping = true
    daemon.running = false
  }
}
