import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";
import type { Simulation, SimulationLinkDatum, SimulationNodeDatum } from "d3-force";
import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useMoneyGraph } from "../../api/hooks";
import type { GraphNode, MoneyGraphResponse } from "../../api/types";
import { ChartOrTable, Legend } from "../../components/chart";
import { Async } from "../../components/ui";
import { PRINCIPAL_COLOR, SERIES } from "../../lib/chart";
import { MONTHS_FULL, brl, pct } from "../../lib/format";

const W = 960;
const H = 520;
const COLUMN = { income: 0.1, account: 0.36, category: 0.64, merchant: 0.9 } as const;
const KIND_COLOR: Record<GraphNode["kind"], string> = { income: SERIES[2], account: SERIES[0], category: SERIES[1], merchant: PRINCIPAL_COLOR };
const KIND_LABEL: Record<GraphNode["kind"], string> = { income: "Receitas", account: "Contas e cartões", category: "Categorias", merchant: "Estabelecimentos" };

interface SimNode extends SimulationNodeDatum, GraphNode {
  r: number;
}
type SimLink = SimulationLinkDatum<SimNode> & { value: number };

function endId(end: string | number | SimNode): string {
  return typeof end === "object" ? end.id : String(end);
}

/** Tudo que está "a montante" (de onde veio o dinheiro) e "a jusante" (para onde foi) de um nó. */
function trace(id: string, links: { source: string; target: string }[]): Set<string> {
  const seen = new Set([id]);
  for (const direction of ["down", "up"] as const) {
    const queue = [id];
    while (queue.length > 0) {
      const current = queue.pop()!;
      for (const l of links) {
        const from = direction === "down" ? l.source : l.target;
        const to = direction === "down" ? l.target : l.source;
        if (from === current && !seen.has(to)) {
          seen.add(to);
          queue.push(to);
        }
      }
    }
  }
  return seen;
}

function Graph({ d }: { d: MoneyGraphResponse }) {
  const svg = useRef<SVGSVGElement>(null);
  const sim = useRef<Simulation<SimNode, SimLink> | null>(null);
  const [, redraw] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const dragging = useRef<{ node: SimNode; moved: boolean } | null>(null);

  const { nodes, links } = useMemo(() => {
    const max = Math.max(...d.nodes.map((n) => n.value), 1);
    const nodes: SimNode[] = d.nodes.map((n, i) => ({
      ...n,
      r: 7 + Math.sqrt(Math.max(n.value, 0) / max) * 26,
      x: W * COLUMN[n.kind],
      y: H / 2 + ((i % 7) - 3) * 30,
    }));
    const links: SimLink[] = d.links.map((l) => ({ source: l.source, target: l.target, value: l.value }));
    return { nodes, links };
  }, [d]);

  useEffect(() => {
    const simulation = forceSimulation<SimNode>(nodes)
      .force("link", forceLink<SimNode, SimLink>(links).id((n) => n.id).distance(70).strength(0.15))
      .force("charge", forceManyBody<SimNode>().strength(-90))
      .force("x", forceX<SimNode>((n) => W * COLUMN[n.kind]).strength(0.9))
      .force("y", forceY<SimNode>(H / 2).strength(0.06))
      .force("collide", forceCollide<SimNode>((n) => n.r + 6).strength(1))
      .alphaDecay(0.05);
    simulation.on("tick", () => {
      for (const n of nodes) {
        n.x = Math.min(W - n.r - 4, Math.max(n.r + 4, n.x ?? 0));
        n.y = Math.min(H - n.r - 4, Math.max(n.r + 4, n.y ?? 0));
      }
      redraw((v) => v + 1);
    });
    sim.current = simulation;
    return () => {
      simulation.stop();
      sim.current = null;
    };
  }, [nodes, links]);

  const focus = hover ?? selected;
  const lit = useMemo(() => (focus ? trace(focus, d.links) : null), [focus, d.links]);
  const maxLink = Math.max(...d.links.map((l) => l.value), 1);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const selectedNode = selected ? byId.get(selected) : undefined;

  const toSvg = (event: ReactPointerEvent): [number, number] => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return [0, 0];
    return [((event.clientX - box.left) / box.width) * W, ((event.clientY - box.top) / box.height) * H];
  };
  const onDown = (event: ReactPointerEvent, node: SimNode) => {
    (event.currentTarget as Element).setPointerCapture(event.pointerId);
    dragging.current = { node, moved: false };
    sim.current?.alphaTarget(0.25).restart();
  };
  const onMove = (event: ReactPointerEvent) => {
    const drag = dragging.current;
    if (!drag) return;
    const [x, y] = toSvg(event);
    drag.moved = true;
    drag.node.fx = x;
    drag.node.fy = y;
  };
  const onUp = () => {
    const drag = dragging.current;
    if (!drag) return;
    drag.node.fx = null;
    drag.node.fy = null;
    if (!drag.moved) setSelected((cur) => (cur === drag.node.id ? null : drag.node.id));
    dragging.current = null;
    sim.current?.alphaTarget(0);
  };

  const share = (n: SimNode): string | null => {
    if (n.kind === "category" || n.kind === "merchant") return d.expense_total > 0 ? pct(n.value / d.expense_total, 1) : null;
    return null;
  };

  return (
    <div>
      <Legend items={(Object.keys(KIND_LABEL) as GraphNode["kind"][]).map((k) => ({ label: KIND_LABEL[k], color: KIND_COLOR[k] }))} />
      <p className="mt-2 text-sm text-muted">Passe o mouse para ver de onde veio e para onde foi o dinheiro. Clique para fixar; arraste para reorganizar.</p>
      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H}`}
        className="mt-2 h-auto w-full touch-pan-y select-none rounded-sm border border-line bg-surface"
        role="img"
        aria-label="Rede do dinheiro: receitas, contas, categorias e estabelecimentos. A tabela abaixo traz os mesmos valores."
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
        onClick={(e) => {
          if (e.target === e.currentTarget) setSelected(null);
        }}
      >
        {(Object.keys(KIND_LABEL) as GraphNode["kind"][]).map((k) => (
          <text key={k} x={W * COLUMN[k]} y={16} textAnchor="middle" fontSize={12} fill="#566370">
            {KIND_LABEL[k]}
          </text>
        ))}
        <g>
          {links.map((l) => {
            const s = l.source as SimNode;
            const t = l.target as SimNode;
            if (typeof s !== "object" || typeof t !== "object") return null;
            const on = lit === null || (lit.has(endId(s)) && lit.has(endId(t)));
            const sx = s.x ?? 0;
            const tx = t.x ?? 0;
            const mid = (sx + tx) / 2;
            return (
              <path
                key={`${endId(s)}>${endId(t)}`}
                d={`M${sx},${s.y ?? 0} C${mid},${s.y ?? 0} ${mid},${t.y ?? 0} ${tx},${t.y ?? 0}`}
                fill="none"
                stroke={KIND_COLOR[s.kind]}
                strokeWidth={1 + (l.value / maxLink) * 14}
                strokeLinecap="round"
                pointerEvents="none"
                opacity={on ? (lit ? 0.55 : 0.28) : 0.05}
              />
            );
          })}
        </g>
        <g>
          {nodes.map((n) => {
            const on = lit === null || lit.has(n.id);
            const showLabel = lit ? lit.has(n.id) : n.r > 14 || n.kind !== "merchant";
            const right = (n.x ?? 0) < W * 0.5;
            return (
              <g
                key={n.id}
                transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                opacity={on ? 1 : 0.18}
                className="cursor-grab active:cursor-grabbing"
                style={{ touchAction: "none" }}
                tabIndex={0}
                role="button"
                aria-label={`${n.label}: ${brl(n.value)}`}
                aria-pressed={selected === n.id}
                onPointerEnter={() => setHover(n.id)}
                onPointerLeave={() => setHover(null)}
                onPointerDown={(e) => onDown(e, n)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelected((cur) => (cur === n.id ? null : n.id));
                  }
                }}
                onFocus={() => setHover(n.id)}
                onBlur={() => setHover(null)}
              >
                <circle r={n.r} fill={KIND_COLOR[n.kind]} stroke={selected === n.id ? "#1d2733" : "#ffffff"} strokeWidth={selected === n.id ? 3 : 1.5} />
                {showLabel && (
                  <text
                    x={right ? n.r + 6 : -n.r - 6}
                    textAnchor={right ? "start" : "end"}
                    dy="0.32em"
                    fontSize={12}
                    fill="#1d2733"
                    paintOrder="stroke"
                    stroke="#ffffff"
                    strokeWidth={3}
                    pointerEvents="none"
                  >
                    {n.label.length > 22 ? `${n.label.slice(0, 21)}…` : n.label}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
      <div className="mt-2 min-h-12 text-sm" aria-live="polite">
        {selectedNode ? (
          <p className="num">
            <strong>{selectedNode.label}</strong>: {brl(selectedNode.value)}
            {share(selectedNode) && <> ({share(selectedNode)} dos gastos)</>}. Destacados: o caminho do dinheiro até aqui e depois daqui.
          </p>
        ) : (
          <p className="text-muted">Nada fixado. Clique em um círculo para acompanhar o caminho do dinheiro.</p>
        )}
      </div>
    </div>
  );
}

export function MoneyGraph({ year, month }: { year: number; month: number | null }) {
  const query = useMoneyGraph(year, month);
  return (
    <div className="mt-14">
      <h3 className="text-xl">A rede do dinheiro</h3>
      <p className="mt-1 max-w-[68ch] text-muted">
        De onde o dinheiro entrou, por quais contas passou e onde acabou, {month ? `em ${MONTHS_FULL[month - 1]} de ${year}` : `em ${year}`}. Quanto maior o círculo e mais grossa a ligação, maior o valor.
      </p>
      <div className={`mt-4 ${query.isPlaceholderData ? "opacity-60" : ""}`}>
        <Async query={query}>
          {(d) =>
            d.nodes.length === 0 ? (
              <p className="text-muted">Nenhuma transação neste período.</p>
            ) : (
              <ChartOrTable
                label="Rede do dinheiro"
                chart={<Graph d={d} />}
                head={["De", "Para", "Valor"]}
                rows={d.links
                  .slice()
                  .sort((a, b) => b.value - a.value)
                  .map((l) => [d.nodes.find((n) => n.id === l.source)?.label ?? l.source, d.nodes.find((n) => n.id === l.target)?.label ?? l.target, brl(l.value)])}
              />
            )
          }
        </Async>
      </div>
    </div>
  );
}
