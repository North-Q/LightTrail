/** M1 记忆页（E7-6 骨架 / E7-7 完善）：器材档案 GET/PUT /api/profile + 事件历史/偏好（示例）。 */

import { useEffect, useState } from "react";
import { useSources } from "../context/SourceContext";

interface Profile {
  camera_body: string;
  lenses: string[];
  preferences: string[];
  common_locations: string[];
  skill_level: string;
  favorite_spots: { name: string; latitude: number; longitude: number; subject?: string }[];
}

const EMPTY_PROFILE: Profile = {
  camera_body: "",
  lenses: [],
  preferences: [],
  common_locations: [],
  skill_level: "",
  favorite_spots: [],
};

const FAKE_PREFERENCES = ["火烧云", "银河", "城市风光", "蓝调时刻"];
const FAKE_EVENTS = [
  { time: "09-07 18:20", summary: "崇明东滩 · 火烧云（评分 62 → 中可接受）" },
  { time: "09-05 23:10", summary: "天荒坪 · 银河（成功，ISO 3200 / 20s）" },
];

export function M1Page() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const { addSource } = useSources();

  useEffect(() => {
    void (async () => {
      try {
        const resp = await fetch("/api/profile");
        if (resp.ok) {
          setProfile((await resp.json()) as Profile);
        } else {
          setProfile(EMPTY_PROFILE);
        }
      } catch {
        setProfile(EMPTY_PROFILE);
      }
      addSource({ name: "本地档案", note: "data/profile.json（GET/PUT /api/profile）" });
    })();
  }, [addSource]);

  async function save(): Promise<void> {
    if (!profile) {
      return;
    }
    setSaving(true);
    setMessage("");
    try {
      const resp = await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
      });
      if (resp.ok) {
        setProfile((await resp.json()) as Profile);
        setMessage("已保存 ✓");
      } else {
        setMessage(`保存失败（${resp.status}）`);
      }
    } catch {
      setMessage("保存失败：请确认后端已启动");
    } finally {
      setSaving(false);
    }
  }

  if (!profile) {
    return <div className="container page-main"><p className="trace-empty">载入档案…</p></div>;
  }

  return (
    <div className="container page-main">
      <header className="page-head">
        <div>
          <h1 className="page-title">M1 · 我的记忆</h1>
          <p className="page-sub">器材档案可读写；事件历史与偏好芯片为示例数据。</p>
        </div>
      </header>

      <section className="grid-2">
        <div className="card">
          <span className="card-kicker">器材档案（可编辑 · GET/PUT /api/profile）</span>
          <div className="gear-field">
            <label htmlFor="camera">机身</label>
            <input id="camera" value={profile.camera_body} onChange={(e) => setProfile({ ...profile, camera_body: e.target.value })} />
          </div>
          <div className="gear-field">
            <label htmlFor="lenses">镜头</label>
            <input id="lenses" value={profile.lenses.join("、")} onChange={(e) => setProfile({ ...profile, lenses: e.target.value.split(/[、,]/).map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="gear-field">
            <label htmlFor="level">水平</label>
            <input id="level" value={profile.skill_level} onChange={(e) => setProfile({ ...profile, skill_level: e.target.value })} />
          </div>
          <div className="gear-field">
            <label htmlFor="locs">常去机位</label>
            <input id="locs" value={profile.common_locations.join("、")} onChange={(e) => setProfile({ ...profile, common_locations: e.target.value.split(/[、,]/).map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="chip-row">
            <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving}>
              {saving ? "保存中…" : "保存档案"}
            </button>
            {message ? <span className="page-sub" style={{ alignSelf: "center" }}>{message}</span> : null}
          </div>
        </div>

        <div className="card">
          <span className="card-kicker">偏好芯片（示例数据）</span>
          <div className="pref-list">
            {FAKE_PREFERENCES.map((item) => (
              <button type="button" className="chip" key={item}>#{item}</button>
            ))}
          </div>
          <span className="card-kicker" style={{ marginTop: 20, display: "block" }}>事件历史（示例数据）</span>
          <ul className="event-list">
            {FAKE_EVENTS.map((item) => (
              <li key={item.time}>
                <span className="src-time">{item.time}</span> {item.summary}
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}
