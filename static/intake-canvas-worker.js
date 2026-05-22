'use strict';

let canvas = null;
let ctx = null;
let dpr = 1;
let logicalW = 0;
let logicalH = 0;
let strokes = [];
let current = null;

// Predicted-events tail: lookahead rendering that hides input latency.
// We snapshot the pixels under the predicted region before drawing predictions
// and restore them before drawing the next batch of real points or on strokeEnd.
let predictedSnapshot = null;  // { imageData, x, y, w, h } in device pixels

self.onmessage = ({ data }) => {
  switch (data.type) {
    case 'init':           return onInit(data);
    case 'grow':           return onGrow(data);
    case 'resize':         return onResize(data);
    case 'strokeStart':    return onStrokeStart(data);
    case 'strokePoints':   return onStrokePoints(data);
    case 'strokePredicted':return onStrokePredicted(data);
    case 'strokeEnd':      return onStrokeEnd();
    case 'undo':           return onUndo();
    case 'clear':          return onClear();
    case 'exportBlob':     return onExportBlob(data);
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
  predictedSnapshot = null;
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
  predictedSnapshot = null;
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
  restorePredicted();
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

function onStrokePredicted({ coords }) {
  if (!current || !coords || coords.length < 2) return;
  // Compute bounding box of the predicted segment so we can snapshot
  // and later restore exactly the pixels we're about to dirty.
  const start = current.lastMid;
  let minX = start.x, maxX = start.x, minY = start.y, maxY = start.y;
  for (let i = 0; i < coords.length; i += 2) {
    if (coords[i]     < minX) minX = coords[i];
    if (coords[i]     > maxX) maxX = coords[i];
    if (coords[i + 1] < minY) minY = coords[i + 1];
    if (coords[i + 1] > maxY) maxY = coords[i + 1];
  }
  const halfW = current.width;  // ample padding for stroke width
  const padX = Math.ceil(halfW + 2);
  const padY = Math.ceil(halfW + 2);
  const dx = Math.max(0, Math.floor((minX - padX) * dpr));
  const dy = Math.max(0, Math.floor((minY - padY) * dpr));
  const dw = Math.min(canvas.width  - dx, Math.ceil((maxX - minX + padX * 2) * dpr));
  const dh = Math.min(canvas.height - dy, Math.ceil((maxY - minY + padY * 2) * dpr));
  if (dw <= 0 || dh <= 0) return;

  restorePredicted();
  try {
    predictedSnapshot = { imageData: ctx.getImageData(dx, dy, dw, dh), x: dx, y: dy, w: dw, h: dh };
  } catch (_) {
    predictedSnapshot = null; // some browsers tainting issues — give up gracefully
  }

  ctx.beginPath();
  ctx.moveTo(start.x, start.y);
  let mx = start.x, my = start.y;
  let prevX = start.x, prevY = start.y;
  for (let i = 0; i < coords.length; i += 2) {
    const nx = coords[i], ny = coords[i + 1];
    mx = (prevX + nx) * 0.5;
    my = (prevY + ny) * 0.5;
    ctx.quadraticCurveTo(prevX, prevY, mx, my);
    prevX = nx; prevY = ny;
  }
  ctx.stroke();
}

function restorePredicted() {
  if (!predictedSnapshot) return;
  // putImageData expects untransformed coords; ctx.scale doesn't affect it.
  ctx.putImageData(predictedSnapshot.imageData, predictedSnapshot.x, predictedSnapshot.y);
  predictedSnapshot = null;
}

function onStrokeEnd() {
  if (!current) return;
  restorePredicted();
  if (current.points.length > 0) strokes.push(current);
  current = null;
}

function onUndo() {
  if (strokes.length === 0) return;
  predictedSnapshot = null;
  strokes.pop();
  redrawAll();
}

function onClear() {
  predictedSnapshot = null;
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
