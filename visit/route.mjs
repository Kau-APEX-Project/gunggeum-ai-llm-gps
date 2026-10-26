// 보행 그래프(servermap/data/paths.geojson) 위 최단 경로.
// 담장 = 엣지 없음. 출입 금지 길은 그래프에서 뺀다.
import { distM } from './judge.mjs'

const closed = (p) => ['no', 'private', 'permit'].includes(p.access) && !['yes', 'designated'].includes(p.foot)

export function buildGraph(paths) {
  const coord = new Map(), adj = new Map()
  for (const f of paths.features) {
    if (closed(f.properties)) continue
    const ids = f.properties.nodes, cs = f.geometry.coordinates
    ids.forEach((id, i) => { coord.set(id, cs[i]); if (!adj.has(id)) adj.set(id, []) })
    for (let i = 1; i < ids.length; i++) {
      const w = distM(cs[i - 1], cs[i])
      adj.get(ids[i - 1]).push([ids[i], w])
      adj.get(ids[i]).push([ids[i - 1], w])
    }
  }
  return { coord, adj }
}

export function nearestNode(g, p, filter = () => true) {
  let best = null, bd = Infinity
  for (const [id, c] of g.coord) {
    const d = distM(p, c)
    if (d < bd && filter(id)) { bd = d; best = id }
  }
  return best
}

const WALK = 1.0 // m/s 관람객 보행 속도 (안내 시간용)
export const minutesOf = (m) => m / WALK / 60

// from 에서 Dijkstra. want 가 있으면 그중 첫 노드에서 멈춤(to), 없으면 끝까지 → 도달 가능한 모든 노드 거리
// ponytail: 배열 스캔 Dijkstra O(n²) — 노드 1천 개라 충분, 수만 개면 힙으로
export function distancesFrom(g, from, want) {
  const dist = new Map([[from, 0]]), prev = new Map(), done = new Set()
  while (true) {
    let u = null, ud = Infinity
    for (const [n, d] of dist) if (!done.has(n) && d < ud) { u = n; ud = d }
    if (u === null || want?.has(u)) return { dist, prev, to: u }
    done.add(u)
    for (const [v, w] of g.adj.get(u)) {
      if (ud + w < (dist.get(v) ?? Infinity)) { dist.set(v, ud + w); prev.set(v, u) }
    }
  }
}

// distancesFrom 결과에서 출발점 → n 경로
function trace(g, { dist, prev }, n) {
  const nodes = [n]
  while (prev.has(nodes[0])) nodes.unshift(prev.get(nodes[0]))
  return { to: n, meters: dist.get(n), nodes, coords: nodes.map((x) => g.coord.get(x)) }
}

// from 에서 targets 중 그래프상 가장 가까운 노드까지 경로. 없으면 null
export function shortestPath(g, from, targets) {
  const d = distancesFrom(g, from, new Set(targets))
  return d.to === null ? null : trace(g, d, d.to)
}

// 그래프 경로 앞뒤에 직선 구간(현재 위치 → 첫 노드, 마지막 노드 → 핀)을 더한 거리·시간
function walkTo(r, here, pin) {
  const meters = distM(here, r.coords[0]) + r.meters + distM(r.coords.at(-1), pin)
  return { ...r, meters, minutes: minutesOf(meters), coords: [...r.coords, pin] }
}

// 길찾기: 현재 위치 → POI 핀 (접근 노드까지 길 따라, 거기서 핀까지 직선)
export function routeTo(g, here, poi) {
  const r = shortestPath(g, nearestNode(g, here), poi.approachNodes)
  return r && walkTo(r, here, poi.pin)
}

// 근처 편의시설: pins(servermap pins.geojson) 중 category(restroom·bench·water) 를 걸어서 가까운 순 k 개.
// 붙어 있는 핀(벤치 5개가 한자리 등)은 sameM 안이면 하나로
export function nearestAmenities(g, here, pins, category, k = 3, sameM = 15) {
  const d = distancesFrom(g, nearestNode(g, here))
  const all = pins.features.filter((f) => f.properties.category === category).map((f) => {
    const pin = f.geometry.coordinates, n = nearestNode(g, pin, (id) => d.dist.has(id))
    return { id: f.properties.id, pin, n, meters: d.dist.get(n) + distM(g.coord.get(n), pin) }
  }).filter((a) => a.n !== null).sort((a, b) => a.meters - b.meters)
  const out = []
  for (const a of all) {
    if (out.length >= k) break
    if (out.some((o) => distM(o.pin, a.pin) < sameM)) continue
    const r = walkTo(trace(g, d, a.n), here, a.pin)
    out.push({ id: a.id, pin: a.pin, meters: r.meters, minutes: r.minutes, coords: r.coords })
  }
  return out
}

// 코스 맞추기: LLM 이 정한 순서(stops)를 그대로 따라 실제 길로 걸어 보고, minutes 분(끝점까지 돌아오는 시간 포함)을 넘는 곳은 뺌.
// stops = [{ id, pin, approachNodes?, stayMin }] — 전각은 접근 노드로, 자연 지점처럼 접근 노드가 없으면 핀에서 가장 가까운 노드로 감.
// pin 이 없으면 모르는 곳. end = 끝점 (기본 출발점, null = 끝점 없음)
export function fitCourse(g, here, stops, minutes, end = here) {
  const fin = end && nearestNode(g, end)
  const back = (n) => (fin ? shortestPath(g, n, [fin]) : { meters: 0, coords: [] })
  const out = [], dropped = []
  let at = nearestNode(g, here), used = 0, meters = 0
  for (const s of stops) {
    if (!s.pin) { dropped.push({ id: s.id, reason: 'unknown' }); continue }
    const nodes = (s.approachNodes || []).filter((n) => g.coord.has(n))
    const r = shortestPath(g, at, nodes.length ? nodes : [nearestNode(g, s.pin)]), b = r && back(r.to)
    if (!b) { dropped.push({ id: s.id, reason: 'path' }); continue }
    const walkMin = minutesOf(r.meters)
    if (used + walkMin + s.stayMin + minutesOf(b.meters) > minutes) { dropped.push({ id: s.id, reason: 'time' }); continue }
    out.push({ id: s.id, walkMin, stayMin: s.stayMin, coords: r.coords })
    used += walkMin + s.stayMin; meters += r.meters; at = r.to
  }
  const b = back(at)
  return {
    stops: out, back: fin ? { walkMin: minutesOf(b.meters), coords: b.coords } : null, // 마지막 곳 → 끝점
    minutes: used + minutesOf(b.meters), meters: meters + b.meters, dropped,
  }
}

// 현재 위치에서 아직 안 간 POI 중 걸어서 가장 가까운 곳까지 경로
export function nextStop(g, here, pois, visited) {
  const owner = new Map()
  for (const poi of pois) if (!visited.has(poi.id)) for (const n of poi.approachNodes) if (g.coord.has(n)) owner.set(n, poi.id)
  if (!owner.size) return null
  const r = shortestPath(g, nearestNode(g, here), owner.keys())
  return r && { poiId: owner.get(r.to), ...r }
}
