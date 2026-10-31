import { useEffect, useRef, useState } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type Snapshot = {
  running?: boolean
  takeover?: boolean
  paused_reason?: string
  last_error?: string
  last_action?: any
  analysis?: string
  recommendation?: string
  game_mode?: "singleplayer" | "multiplayer"
  windows?: Array<{ hwnd: number; title: string }>
  ledger?: { initialized?: boolean; total?: number | null; p_live?: number | null;
    p_blank?: number | null; next_known?: string | null; recommendation?: string }
}

function unwrap(envelope: any): Snapshot {
  let value = envelope
  for (let i = 0; i < 5 && value && typeof value === "object"; i++) {
    if (value.error && value.ok === false) throw new Error(String(value.error))
    if ("running" in value || "ledger" in value || "takeover" in value) return value
    if (value.result && typeof value.result === "object") value = value.result
    else if (value.data && typeof value.data === "object") value = value.data
    else return value
  }
  return value || {}
}

const panel: any = { background: "#161a20", border: "1px solid #303640", borderRadius: 16, padding: 20 }
const label: any = { fontSize: 12, color: "#9ca7b8", letterSpacing: ".04em", marginBottom: 8 }
const input: any = { background: "#101318", color: "#edf0f5", border: "1px solid #3c4553", borderRadius: 8,
  padding: "9px 10px", fontSize: 14, minWidth: 0, width: "100%", boxSizing: "border-box" }
const button: any = { background: "#272f3b", color: "#edf0f5", border: "1px solid #455063", borderRadius: 9,
  padding: "10px 14px", cursor: "pointer", fontSize: 13, fontWeight: 600 }
const row: any = { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }
const pct = (value: number | null | undefined) => value == null ? "—" : (value * 100).toFixed(1) + "%"

export default function BuckshotPanel(props: PluginSurfaceProps) {
  const [state, setState] = useState<Snapshot>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [hwnd, setHwnd] = useState("0")
  const [live, setLive] = useState("3")
  const [blank, setBlank] = useState("3")
  const disposed = useRef(false)
  const fetching = useRef(false)
  const changing = useRef(false)
  const serial = useRef(0)

  const refresh = async () => {
    if (disposed.current || fetching.current || changing.current) return
    fetching.current = true
    const current = serial.current
    try {
      const snapshot = unwrap(await props.api.call("status"))
      if (!disposed.current && current === serial.current) { setState(snapshot); setError("") }
    } catch (exc: any) {
      if (!disposed.current && current === serial.current) setError(String(exc?.message || exc))
    } finally { fetching.current = false }
  }

  const act = async (id: string, args: Record<string, any> = {}) => {
    if (changing.current || disposed.current) return
    changing.current = true
    serial.current += 1
    setBusy(true)
    setError("")
    try {
      const snapshot = unwrap(await props.api.call(id, args))
      if (!disposed.current) setState(snapshot)
    } catch (exc: any) {
      if (!disposed.current) setError(String(exc?.message || exc))
    } finally {
      changing.current = false
      if (!disposed.current) setBusy(false)
    }
  }

  useEffect(() => {
    disposed.current = false
    refresh()
    const timer = window.setInterval(refresh, 2000)
    return () => { disposed.current = true; window.clearInterval(timer) }
  }, [])

  const ledger = state.ledger || {}
  const takeover = Boolean(state.takeover)
  const running = Boolean(state.running)
  const available = ledger.initialized && (ledger.total || 0) > 0
  const actionText = typeof state.last_action === "string" ? state.last_action
    : state.last_action?.reason || state.last_action?.action || "尚无操作"
  return (
    <main style={{ color: "#edf0f5", background: "#101318", padding: 24, minHeight: "100vh", boxSizing: "border-box",
      fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif', fontSize: 14, lineHeight: 1.6, colorScheme: "dark" }}>
      <style>{"button:disabled { opacity: .42; cursor: default !important; } input:disabled, select:disabled { opacity: .65; }"}</style>
      <div style={{ maxWidth: 980, margin: "0 auto", display: "grid", gap: 16 }}>
        <header style={{ ...row, justifyContent: "space-between", marginBottom: 6 }}>
          <div>
            <div style={{ ...label, color: "#d9a26c", margin: 0 }}>N.E.K.O × BUCKSHOT ROULETTE</div>
            <h1 style={{ fontSize: 26, margin: "4px 0", fontWeight: 650 }}>一起看牌局，也能交给她</h1>
            <div style={{ color: "#a8b3c4" }}>看画面、记弹药，下一步由你决定。</div>
          </div>
          <span style={{ padding: "5px 12px", borderRadius: 24, fontSize: 12,
            color: running ? "#a7e1ba" : "#b0b8c5", background: running ? "#1b3728" : "#262d37" }}>
            {running ? (state.paused_reason ? "等待游戏画面" : "正在陪玩") : "已暂停"}
          </span>
        </header>

        <section style={{ ...panel, borderColor: takeover ? "#a46e3c" : "#3b5067" }}>
          <div style={{ ...row, justifyContent: "space-between", gap: 20 }}>
            <div style={{ flex: "1 1 230px" }}>
              <div style={{ ...label, color: takeover ? "#edbd8c" : "#98bcdd" }}>当前模式</div>
              <h2 style={{ margin: "0 0 6px", fontSize: 21 }}>{takeover ? "猫娘接管" : "分析与建议"}</h2>
              <div style={{ color: "#b3bdcb" }}>{takeover ? "她会观察局面并操作游戏，你可以随时关闭接管。" : "她会分析概率和道具，给出建议，所有点击由你完成。"}</div>
            </div>
            <label style={{ ...row, cursor: busy ? "wait" : "pointer", fontSize: 16, fontWeight: 600,
              padding: "14px 18px", borderRadius: 12, background: "#101318", border: "1px solid #3c4553" }}>
              <input type="checkbox" role="switch" aria-label="是否接管赌局" checked={takeover} disabled={busy}
                onChange={(event: any) => act("set_takeover", { enabled: event.target.checked })}
                style={{ width: 23, height: 23, accentColor: "#dca66b", marginRight: 4 }} />
              是否接管赌局
            </label>
          </div>
          <div style={{ ...row, marginTop: 20 }}>
            <label style={{ flex: "1 1 180px" }}><div style={label}>游戏模式</div>
              <select aria-label="游戏模式" style={input} disabled={busy} value={state.game_mode || "singleplayer"}
                onChange={(event: any) => act("set_game_mode", { mode: event.target.value })}>
                <option value="singleplayer">单机</option><option value="multiplayer">联机</option>
              </select>
            </label>
          </div>
          <div style={{ ...row, marginTop: 20 }}>
            <select aria-label="选择游戏窗口" value={hwnd} onChange={(event: any) => setHwnd(event.target.value)} disabled={busy || running}
              style={{ ...input, width: "auto", flex: "1 1 220px" }}>
              <option value="0">自动选择 Buckshot Roulette 窗口</option>
              {(state.windows || []).map(game => <option key={game.hwnd} value={String(game.hwnd)}>{game.title}</option>)}
            </select>
            <button style={{ ...button, background: "#dca66b", color: "#21180e", borderColor: "#dca66b" }}
              disabled={busy || running} onClick={() => act("start", { hwnd: Number(hwnd) })}>{busy ? "处理中…" : "开始陪玩"}</button>
            <button style={button} disabled={busy || !running} onClick={() => act("stop")}>暂停</button>
          </div>
          <div style={{ color: "#8996a7", fontSize: 12, marginTop: 12 }}>保持游戏在前台。按 F8 随时停止陪玩；关闭接管后继续分析。</div>
          {state.paused_reason && <div role="status" style={{ color: "#edc69d", marginTop: 12 }}>{state.paused_reason}</div>}
        </section>

        {(error || state.last_error) && <div role="alert" style={{ ...panel, borderColor: "#795144", color: "#f0b6a2" }}>{error || state.last_error}</div>}

        <section style={{ ...panel, borderColor: "#40556b", background: "#17212b" }}>
          <h2 style={{ fontSize: 17, margin: "0 0 12px" }}>猫娘的判断</h2>
          <div style={{ color: "#c5d1df", whiteSpace: "pre-wrap" }}>
            {state.analysis || "开始陪玩后，她会根据游戏画面分析当前局面。未看清的信息会明确保留为未知。"}
          </div>
          {state.recommendation && <div style={{ marginTop: 16, borderTop: "1px solid #37495b", paddingTop: 14 }}>
            <div style={{ ...label, color: "#a9c9e6" }}>下一步建议</div>
            <div style={{ color: "#edf0f5", whiteSpace: "pre-wrap" }}>{state.recommendation}</div>
          </div>}
        </section>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
          <section style={panel}>
            <h2 style={{ fontSize: 17, margin: "0 0 18px" }}>下一发的概率</h2>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
              <div><div style={label}>实弹</div><div style={{ fontSize: 34, fontWeight: 650, color: "#e5ac8e" }}>{pct(ledger.p_live)}</div></div>
              <div><div style={label}>空包弹</div><div style={{ fontSize: 34, fontWeight: 650, color: "#a2c5dc" }}>{pct(ledger.p_blank)}</div></div>
            </div>
            <div style={{ height: 7, borderRadius: 4, background: "#2c4455", margin: "16px 0" }}>
              <div style={{ width: `${(ledger.p_live || 0) * 100}%`, height: "100%", borderRadius: 4, background: "#cf9370" }} />
            </div>
            <div style={{ color: "#acb7c6" }}>剩余 {ledger.total == null ? "未知" : `${ledger.total} 发`}
              {ledger.next_known ? ` · 下一发已知：${ledger.next_known === "live" ? "实弹" : "空包弹"}` : ""}</div>
            <p style={{ margin: "14px 0 0", color: "#c3ccd8", fontSize: 13 }}>{ledger.recommendation || "先看清装弹数量，再判断下一发。"}</p>
          </section>
          <section style={panel}>
            <h2 style={{ fontSize: 17, margin: "0 0 12px" }}>弹药记录</h2>
            <div style={{ color: "#a5b0c0", fontSize: 13, marginBottom: 14 }}>猫娘会根据看到的画面更新记录。识别遗漏时，可以在这里校正。</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <label><div style={label}>本轮装入实弹</div><input aria-label="本轮实弹数" style={input} type="number" min="0" max="16" value={live}
                onChange={(event: any) => setLive(event.target.value)} /></label>
              <label><div style={label}>本轮装入空包弹</div><input aria-label="本轮空包弹数" style={input} type="number" min="0" max="16" value={blank}
                onChange={(event: any) => setBlank(event.target.value)} /></label>
            </div>
            <div style={row}>
              <button style={button} disabled={busy || live === "" || blank === ""} onClick={() => act("reset_ammo", { live: Number(live), blank: Number(blank) })}>记录新一轮装弹</button>
              <button style={button} disabled={busy || !ledger.initialized} onClick={() => act("clear_ammo")}>清空记录</button>
            </div>
          </section>
        </div>

        <section style={panel}>
          <h2 style={{ fontSize: 17, margin: "0 0 6px" }}>校正刚刚发生的事</h2>
          <p style={{ margin: "0 0 16px", color: "#9eabbd", fontSize: 13 }}>每发射出或退膛的弹只记一次；同一发不要重复记录。</p>
          <div style={{ ...row, alignItems: "flex-start", gap: 20 }}>
            <div><div style={label}>已射出 / 已退膛</div><div style={row}>
              <button style={button} disabled={busy || !available} onClick={() => act("record_shell", { kind: "live" })}>一发实弹</button>
              <button style={button} disabled={busy || !available} onClick={() => act("record_shell", { kind: "blank" })}>一发空包弹</button>
              <button style={button} disabled={busy || !available} onClick={() => act("record_shell", { kind: "unknown" })}>一发，类型不明</button>
            </div></div>
            <div><div style={label}>已确认的下一发</div><div style={row}>
              <button style={button} disabled={busy || !available} onClick={() => act("reveal_shell", { kind: "live" })}>实弹</button>
              <button style={button} disabled={busy || !available} onClick={() => act("reveal_shell", { kind: "blank" })}>空包弹</button>
              <button style={button} disabled={busy || !available} onClick={() => act("invert_shell")}>已使用逆转器</button>
            </div></div>
          </div>
        </section>
        <footer style={{ color: "#8492a5", fontSize: 12, padding: "0 2px" }}>最近操作：{actionText}。未看清的弹药会保留为未知；概率取决于记录是否完整。</footer>
      </div>
    </main>
  )
}
