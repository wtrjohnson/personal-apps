'use strict';

let canvas = null;
let ctx = null;
let dpr = 1;
let logicalW = 0;
let logicalH = 0;
let strokes = [];
let current = null;

self.onmessage = ({ data }) => {
  switch (data.type) {
    case 'init':         return onInit(data);
    case 'grow':         return onGrow(data);
    case 'resize':       return onResize(data);
    case 'strokeStart':  return onStrokeStart(data);
    case 'strokePoints': return onStrokePoints(data);
    case 'strokeEnd':    return onStrokeEnd();
    case 'undo':         return onUndo();
    case 'clear':        return onClear();
    case 'exportBlob':   return onExportBlob(data);
  }
};

function onInit({ offscreen, dpr: d, width, height }) {
  canvas = offscreen;
  dpr = d;
  logicalW = width;
  logicalH = height;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx = canvas.getContext('2d', { desynchronized: true, alpha: false });
  ctx.scale(dpr, dpr);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, logicalW, logicalH);
}

function onGrow({ newHeight }) {
  const oldH = logicalH;
  const snap = new OffscreenCanvas(canvas.width, canvas.height);
  snap.getContext('2d').drawImage(canvas, 0, 0);

  logicalH = newHeight;
  canvas.height = Math.round(newHeight * dpr);
  ctx = canvas.getContext('2d', { desynchronized: true, alpha: false });
  ctx.scale(dpr, dpr);
  ctx.drawImage(snap, 0, 0);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, oldH, logicalW, newHeight - oldH);
}

function onResize({ width, height }) {
  logicalW = width;
  logicalH = height;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx = canvas.getContext('2d', { desynchronized: true, alpha: false });
  ctx.scale(dpr, dpr);
  redrawAll();
}

function onStrokeStart({ color, width, x, y }) {
  current = { color, width, points: [{ x, y }], lastMid: { x, y } };
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.arc(x, y, width / 2, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function onStrokePoints({ coords }) {
  if (!current) return;
  ctx.beginPath();
  ctx.moveTo(current.lastMid.x, current.lastMid.y);
  for (let i = 0; i < coords.length; i += 2) {
    const nx = coords[i], ny = coords[i + 1];
    const prev = current.points[current.points.length - 1];
    current.points.push({ x: nx, y: ny });
    const mx = (prev.x + nx) * 0.5;
    const my = (prev.y + ny) * 0.5;
    ctx.quadraticCurveTo(prev.x, prev.y, mx, my);
    current.lastMid.x = mx;
    current.lastMid.y = my;
  }
  ctx.stroke();
}

function onStrokeEnd() {
  if (!current) return;
  if (current.points.length > 0) strokes.push(current);
  current = null;
}

function onUndo() {
  if (strokes.length === 0) return;
  strokes.pop();
  redrawAll();
}

function onClear() {
  strokes = [];
  current = null;
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, logicalW, logicalH);
}

async function onExportBlob({ id }) {
  const blob = await canvas.convertToBlob({ type: 'image/png' });
  const ab = await blob.arrayBuffer();
  self.postMessage({ type: 'exportResult', id, ab }, [ab]);
}

function redrawAll() {
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, logicalW, logicalH);
  for (const s of strokes) drawStroke(s);
}

function drawStroke(s) {
  const pts = s.points;
  if (pts.length === 0) return;
  if (pts.length === 1) {
    ctx.beginPath();
    ctx.arc(pts[0].x, pts[0].y, s.width / 2, 0, Math.PI * 2);
    ctx.fillStyle = s.color;
    ctx.fill();
    return;
  }
  ctx.beginPath();
  ctx.strokeStyle = s.color;
  ctx.lineWidth = s.width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.moveTo((pts[0].x + pts[1].x) * 0.5, (pts[0].y + pts[1].y) * 0.5);
  for (let i = 1; i < pts.length - 1; i++) {
    const mx = (pts[i].x + pts[i + 1].x) * 0.5;
    const my = (pts[i].y + pts[i + 1].y) * 0.5;
    ctx.quadraticCurveTo(pts[i].x, pts[i].y, mx, my);
  }
  ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
  ctx.stroke();
}
