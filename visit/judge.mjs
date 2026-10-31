// 宮今 방문 판정 — 위치 샘플을 넣으면 enter/audio/stamp/exit 이벤트를 돌려준다.
// 설계: 宮今 GPS 방문 판정 로직 설계.pdf. 숫자는 현장 튜닝 전 출발값.

export const DEFAULTS = {
  maxAccuracy: 25,       // m, 이보다 나쁜 샘플은 버림
  stampAccuracy: 15,     // m, 스탬프 체류는 이보다 좋은 샘플만 인정
  maxSpeed: 5,           // m/s, 직전 유효 샘플 대비 이보다 빠르면 점프로 보고 버림
  maxGap: 5,             // s, 유효 샘플 공백이 이보다 길면 체류 시간 리셋
  exitMargin: 10,        // m, 존 경계에서 이만큼 벗어나야 이탈 (히스테리시스)
  exitSamples: 2,        // 이탈 판정에 필요한 연속 샘플 수
  dwellAudioSec: 7,
  dwellStampSec: 25,
  audioCooldownSec: 30 * 60,
}

// 경복궁 범위(수 km)에선 위도 고정 평면 근사로 충분
const R = 6371008.8, RAD = Math.PI / 180
export function toXY([lng, lat], lat0) {
  return [lng * RAD * R * Math.cos(lat0 * RAD), lat * RAD * R]
}
export const distM = (a, b) => {
  const [ax, ay] = toXY(a, a[1]), [bx, by] = toXY(b, a[1])
  return Math.hypot(ax - bx, ay - by)
}

export function inRing(p, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j]
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
// 폴리곤(구멍 포함) 안이면 0, 밖이면 경계까지 거리(m)
export function distToPolygon(p, rings) {
  if (inRing(p, rings[0]) && !rings.slice(1).some((h) => inRing(p, h))) return 0
  const [px, py] = toXY(p, p[1])
  let best = Infinity
  for (const ring of rings) {
    for (let i = 1; i < ring.length; i++) {
      const [ax, ay] = toXY(ring[i - 1], p[1]), [bx, by] = toXY(ring[i], p[1])
      const dx = bx - ax, dy = by - ay
      const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy || 1)))
      best = Math.min(best, Math.hypot(px - ax - t * dx, py - ay - t * dy))
    }
  }
  return best
}

/**
 * zones: GeoJSON FeatureCollection (Polygon, properties.id)
 * pois:  [{ id, zoneIds: [], pin?, dwellAudioSec?, dwellStampSec? }] — pin: 해설이 여럿 겹칠 때 가장 가까운 곳을 고름
 * saved: { visited: [poiId], lastAudio: { poiId: t(ms) } } — 앱 재시작 시 복원용
 * update({ lat, lng, accuracy, t(ms) }) → [{ type, poiId, t }]
 */
export function createJudge(zones, pois, opts = {}, saved = {}) {
  const o = { ...DEFAULTS, ...opts }
  const zoneById = new Map(zones.features.map((f) => [f.properties.id, f.geometry.coordinates]))
  const st = new Map()
  for (const poi of pois) {
    for (const z of poi.zoneIds) if (!zoneById.has(z)) throw new Error(`POI ${poi.id}: 없는 존 ${z}`)
    st.set(poi.id, { poi, state: 'OUTSIDE', dwell: 0, stampDwell: 0, lastIn: 0, far: 0 })
  }
  const visited = new Set(saved.visited || [])
  const lastAudio = { ...saved.lastAudio }
  let last = null // 마지막 유효 샘플

  function update({ lat, lng, accuracy, t }) {
    if (accuracy > o.maxAccuracy) return []
    const p = [lng, lat]
    if (last && distM(last.p, p) / Math.max((t - last.t) / 1000, 1e-3) > o.maxSpeed) return []
    const dt = last && t - last.t <= o.maxGap * 1000 ? (t - last.t) / 1000 : 0 // 긴 공백은 체류에 안 셈
    last = { p, t }

    const ev = [], aud = [] // aud: 이번 샘플에 해설 조건을 채운 POI (존을 같이 쓰면 여럿)
    const zd = new Map() // 이번 샘플의 존별 거리 캐시
    const dist = (z) => (zd.has(z) ? zd.get(z) : zd.set(z, distToPolygon(p, zoneById.get(z))).get(z))

    for (const s of st.values()) {
      const { poi } = s
      const d = Math.min(...poi.zoneIds.map(dist))
      const audioSec = poi.dwellAudioSec ?? o.dwellAudioSec
      const stampSec = poi.dwellStampSec ?? o.dwellStampSec

      // 존 근처에서 한참 샘플이 없다가 밖에서 잡히면 이탈
      if (s.state !== 'OUTSIDE' && d !== 0 && (t - s.lastIn) / 1000 > o.maxGap) {
        s.state = 'OUTSIDE'
        ev.push({ type: 'exit', poiId: poi.id, t })
      }

      if (d > o.exitMargin) {
        if (s.state !== 'OUTSIDE' && ++s.far >= o.exitSamples) {
          s.state = 'OUTSIDE'
          ev.push({ type: 'exit', poiId: poi.id, t })
        }
        continue
      }
      s.far = 0
      s.lastIn = t
      if (d !== 0) continue // 경계 띠: 상태만 유지, 체류는 안 셈

      if (s.state === 'OUTSIDE') {
        s.state = 'ENTERING'
        s.dwell = s.stampDwell = 0
        ev.push({ type: 'enter', poiId: poi.id, t })
      } else {
        s.dwell += dt
        if (accuracy <= o.stampAccuracy) s.stampDwell += dt // 스탬프는 정확도 좋은 샘플만
      }

      if (s.state === 'ENTERING' && s.dwell >= audioSec) {
        s.state = 'INSIDE'
        if (!(t - (lastAudio[poi.id] ?? -Infinity) < o.audioCooldownSec * 1000)) aud.push(poi)
      }
      if (s.state === 'INSIDE' && !visited.has(poi.id) && s.stampDwell >= stampSec) {
        visited.add(poi.id)
        ev.push({ type: 'stamp', poiId: poi.id, t })
      }
    }
    // 해설은 한 번에 하나: 핀이 가장 가까운 곳 (근정전 마당 = 근정전·조정·내부가 한 존). 나머지는 카드에서 들음
    if (aud.length) {
      const near = (q) => (q.pin ? distM(p, q.pin) : Infinity)
      const poi = aud.reduce((a, b) => (near(b) < near(a) ? b : a))
      lastAudio[poi.id] = t
      ev.push({ type: 'audio', poiId: poi.id, t })
    }
    return ev
  }

  return {
    update,
    saved: () => ({ visited: [...visited], lastAudio: { ...lastAudio } }),
    // 화면 표시용 현재 상태
    peek: () => [...st.values()].map(({ poi, state, dwell, stampDwell }) => ({ id: poi.id, state, dwell, stampDwell, visited: visited.has(poi.id) })),
  }
}
