// Graph view (d3 force layout).

import { $, api } from "./util.js";
import { state } from "./state.js";
import { openDoc } from "./viewer.js";

export const EXT_COLOR = { ".md": "#4b5563", ".markdown": "#4b5563", ".pdf": "#d14343", ".docx": "#2f6fdf", ".pptx": "#d9741c",
  ".xlsx": "#1f8f4e", ".csv": "#1f8f4e", ".html": "#7c4dcc", ".htm": "#7c4dcc", ".txt": "#8a94a6" };

export async function renderGraph() {
  const svgEl = $("#graph");
  if (!state.status?.notes_dir) { $("#graph-info").textContent = "Choose a notes folder first."; return; }
  const similar = $("#graph-similar").checked;
  const key = `${state.indexVersion}:${similar}`;
  if (!state.graph || state.graph.key !== key) {
    $("#graph-info").textContent = "Loading…";
    try { state.graph = { key, data: await api("/api/graph?similar=" + similar) }; }
    catch (e) { $("#graph-info").textContent = e.message; return; }
  }
  let nodes = state.graph.data.nodes.map(n => ({ ...n }));
  let edges = state.graph.data.edges.map(e => ({ ...e }));
  const deg = {};
  edges.forEach(e => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
  if ($("#graph-local").checked && state.currentDoc) {
    const keep = new Set([state.currentDoc]);
    for (let hop = 0; hop < 2; hop++) {
      for (const e of edges) {
        if (keep.has(e.source)) keep.add(e.target);
        else if (keep.has(e.target)) keep.add(e.source);
      }
    }
    nodes = nodes.filter(n => keep.has(n.id));
    edges = edges.filter(e => keep.has(e.source) && keep.has(e.target));
  } else if (!$("#graph-orphans").checked) {
    nodes = nodes.filter(n => deg[n.id]);
  }
  const ids = new Set(nodes.map(n => n.id));
  edges = edges.filter(e => ids.has(e.source) && ids.has(e.target));
  $("#graph-info").textContent = `${nodes.length} notes · ${edges.filter(e => e.type === "link").length} links` +
    (similar ? ` · ${edges.filter(e => e.type === "similar").length} similar` : "");

  const svg = d3.select(svgEl);
  svg.selectAll("*").remove();
  const { width, height } = svgEl.getBoundingClientRect();
  const root = svg.append("g");
  const zoom = d3.zoom().scaleExtent([0.2, 6]).on("zoom", ev => {
    root.attr("transform", ev.transform);
    root.classed("zoomed", ev.transform.k > 1.6);
    label.attr("display", d => (ev.transform.k > 1.6 || d.big) ? null : "none");
  });
  svg.call(zoom);

  const radius = d => 4 + Math.sqrt(deg[d.id] || 0) * 2.5;
  const labelAll = nodes.length <= 60;  // small vaults: label everything; big ones: hubs only until zoomed
  nodes.forEach(n => (n.big = labelAll || (deg[n.id] || 0) >= 3 || n.id === state.currentDoc));
  const link = root.append("g").selectAll("line").data(edges).join("line")
    .attr("class", d => "link " + d.type).attr("stroke-width", d => d.type === "link" ? 1.5 : 1);
  const node = root.append("g").selectAll("g").data(nodes).join("g")
    .attr("class", d => "node" + (d.id === state.currentDoc ? " current" : ""));
  node.append("circle").attr("r", radius).attr("fill", d => EXT_COLOR[d.ext] || "#8a94a6");
  node.append("title").text(d => `${d.title}\n${d.id}`);
  const label = node.append("text").text(d => d.title).attr("x", d => radius(d) + 3).attr("y", 4)
    .attr("display", d => d.big ? null : "none");

  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id(d => d.id).distance(d => d.type === "link" ? 60 : 90).strength(d => d.type === "link" ? 0.6 : 0.15))
    .force("charge", d3.forceManyBody().strength(-140))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("x", d3.forceX(width / 2).strength(0.04)).force("y", d3.forceY(height / 2).strength(0.04))
    .force("collide", d3.forceCollide(d => radius(d) + 4))
    .on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y).attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
    });
  state.graphSim?.stop();
  state.graphSim = sim;

  node.call(d3.drag()
    .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
    .on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
  node.on("click", (ev, d) => openDoc(d.id));
  node.on("mouseenter", (ev, d) => {
    const near = new Set([d.id]);
    edges.forEach(e => { if (e.source.id === d.id) near.add(e.target.id); if (e.target.id === d.id) near.add(e.source.id); });
    node.classed("dim", n => !near.has(n.id));
    link.classed("dim", e => e.source.id !== d.id && e.target.id !== d.id);
    label.attr("display", n => near.has(n.id) || n.big ? null : "none");
  }).on("mouseleave", () => { node.classed("dim", false); link.classed("dim", false); applyGraphFilter(); label.attr("display", n => n.big ? null : "none"); });

  function applyGraphFilter() {
    const q = $("#graph-filter").value.trim().toLowerCase();
    node.classed("hit", d => q && (d.title.toLowerCase().includes(q) || d.id.toLowerCase().includes(q)));
    if (q) node.classed("dim", d => !(d.title.toLowerCase().includes(q) || d.id.toLowerCase().includes(q)));
  }
  state.applyGraphFilter = applyGraphFilter;
  applyGraphFilter();
}

export function bindGraph() {
  $("#graph-filter").oninput = () => state.applyGraphFilter?.();
  for (const id of ["#graph-similar", "#graph-orphans", "#graph-local"]) $(id).onchange = renderGraph;
}
