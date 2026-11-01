const escape = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const shade = (hex, amount) => {
  const value = parseInt(hex.slice(1), 16);
  return '#' + [value >> 16, (value >> 8) & 255, value & 255]
    .map(channel => Math.max(0, Math.min(255, channel + amount)).toString(16).padStart(2, '0')).join('');
};

// Small, code-native brick models stay crisp without additional image requests.
export const cameraIllustration = `<svg class="upload-camera" viewBox="0 0 160 140" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="camera-shell" x2="0" y2="1"><stop stop-color="#f4f0e3"/><stop offset="1" stop-color="#d6d0bb"/></linearGradient>
    <linearGradient id="camera-side" x2="1" y2="1"><stop stop-color="#b8bfaa"/><stop offset="1" stop-color="#87937e"/></linearGradient>
    <linearGradient id="camera-lens" x2="1" y2="1"><stop stop-color="#7d8b88"/><stop offset=".45" stop-color="#394c4e"/><stop offset="1" stop-color="#243336"/></linearGradient>
    <radialGradient id="camera-glass" cx=".35" cy=".3"><stop stop-color="#87b1b5"/><stop offset=".4" stop-color="#476f76"/><stop offset="1" stop-color="#192b31"/></radialGradient>
  </defs>
  <ellipse cx="82" cy="121" rx="51" ry="8" fill="#48523e" opacity=".1"/>
  <path d="m27 51 79 21v42L27 93Z" fill="url(#camera-shell)" stroke="#bfc2ad" stroke-linejoin="round"/>
  <path d="m106 72 29-18v42l-29 18Z" fill="url(#camera-side)" stroke="#87917b" stroke-linejoin="round"/>
  <path d="m27 51 29-18 79 21-29 18Z" fill="#ececdb" stroke="#c0c7b1" stroke-linejoin="round"/>
  <path d="m27 91 79 21v8L27 99Z" fill="#4c5b50"/>
  <path d="m106 112 29-18v8l-29 18Z" fill="#37493e"/>
  <g fill="none" stroke="#afb6a0" stroke-width="1" opacity=".85">
    <path d="m27 72 79 21 29-18M48 57v20m35-11v20m-45-11v20m55-5v20M120 63v21m5-4v22"/>
  </g>
  <g fill="#d9deca" stroke="#b1bba1" stroke-width=".7">
    <path d="M43 41v3c0 4 13 4 13 0v-3"/><ellipse cx="49.5" cy="41" rx="6.5" ry="3.3" fill="#f0f1e2"/>
    <path d="M61 46v3c0 4 13 4 13 0v-3"/><ellipse cx="67.5" cy="46" rx="6.5" ry="3.3" fill="#f0f1e2"/>
    <path d="M101 49v3c0 4 13 4 13 0v-3"/><ellipse cx="107.5" cy="49" rx="6.5" ry="3.3" fill="#f0f1e2"/>
  </g>
  <path d="m69 28 15-9 25 7-15 9v15l-25-7Z" fill="#657461" stroke="#4f6250" stroke-linejoin="round"/>
  <path d="m69 28 15-9 25 7-15 9Z" fill="#a7b49a"/>
  <path d="m94 35 15-9v15l-15 9Z" fill="#425743"/>
  <path d="m74 31 14 4v8l-14-4Z" fill="#253d3e"/>
  <path d="m76 32 8 2-8 3Z" fill="#94b9b2" opacity=".7"/>
  <path d="M115 40v5c0 5 15 5 15 0v-5" fill="#b62d28"/>
  <ellipse cx="122.5" cy="40" rx="7.5" ry="4" fill="#e24c3b" stroke="#bd3329"/>
  <ellipse cx="119.5" cy="38.7" rx="3.2" ry="1.1" fill="#ffb4a1" opacity=".6"/>
  <path d="m32 58 12 3v9l-12-3Z" fill="#faf7ed" stroke="#c5c7b5"/>
  <path d="m106 80 4 1v20l-4 2Z" fill="#4e604f"/>
  <ellipse cx="74" cy="82" rx="25" ry="27" fill="#354a47" transform="rotate(-15 74 82)"/>
  <path d="m66 57 12-1c18 1 25 38 8 48l-14 4Z" fill="#3d514e"/>
  <ellipse cx="66" cy="83" rx="24" ry="26" fill="url(#camera-lens)" stroke="#82908a" stroke-width="1.2" transform="rotate(-15 66 83)"/>
  <ellipse cx="66" cy="83" rx="18.5" ry="20.5" fill="#192d31" stroke="#9aa8a0" stroke-width="1.5" transform="rotate(-15 66 83)"/>
  <ellipse cx="65" cy="82" rx="13.5" ry="15.5" fill="url(#camera-glass)" stroke="#4b686b" transform="rotate(-15 65 82)"/>
  <ellipse cx="61" cy="75" rx="4" ry="7" fill="#cce2d8" opacity=".5" transform="rotate(33 61 75)"/>
  <circle cx="70" cy="89" r="2.2" fill="#a8c5bd" opacity=".5"/>
</svg>`;

export function colorSwatch(option, action, selected, scope = 'palette') {
  const color = option.hex;
  const id = `stud-${scope}-${color.slice(1)}`;
  return `<button type="button" class="swatch brick-swatch" data-action="${action}" data-color="${color}" aria-label="${escape(option.name)}" aria-pressed="${selected}" title="${escape(option.name)}">
    <svg class="stud-icon" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${shade(color, 35)}"/><stop offset="1" stop-color="${color}"/></linearGradient></defs>
      <ellipse cx="24" cy="37" rx="16" ry="5" fill="#293329" opacity=".12"/>
      <path d="M7 23v7c0 7 34 7 34 0v-7Z" fill="${shade(color, -35)}"/>
      <ellipse cx="24" cy="23" rx="17" ry="12" fill="url(#${id})" stroke="${shade(color, -12)}" stroke-width=".45"/>
      <path d="M14 15v9c0 7 20 7 20 0v-9Z" fill="${shade(color, -18)}"/>
      <ellipse cx="24" cy="15" rx="10" ry="8" fill="url(#${id})" stroke="${shade(color, 45)}" stroke-width=".6"/>
      <path d="M17 12c3-4 8-4 12-2" fill="none" stroke="#fff" stroke-opacity=".22" stroke-linecap="round"/>
    </svg>
    <span class="swatch-check" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="m4 8 2.5 2.5L12 5"/></svg></span>
  </button>`;
}

function directionModel(degrees, selected) {
  const radians = degrees * Math.PI / 180, cosine = Math.cos(radians), sine = Math.sin(radians);
  const rotate = ([x, y, z]) => [x * cosine + z * sine, y, z * cosine - x * sine];
  const project = point => { const [x, y, z] = rotate(point); return [32 + (x - z) * 10, 29 + (x + z) * 4.5 - y * 14]; };
  const points = vertices => vertices.map(point => project(point).map(value => value.toFixed(2)).join(',')).join(' ');
  const color = selected ? '#ce4034' : '#929a90';
  const polygon = (vertices, fill) => `<polygon points="${points(vertices)}" fill="${fill}" stroke="${shade(color, -35)}" stroke-width=".65" stroke-linejoin="round"/>`;
  const w = 1.3, d = .6, h = .8;
  const faces = [
    { normal:[0,0,1], corners:[[-w,0,d],[w,0,d],[w,h,d],[-w,h,d]], front:true },
    { normal:[1,0,0], corners:[[w,0,d],[w,0,-d],[w,h,-d],[w,h,d]] },
    { normal:[0,0,-1], corners:[[w,0,-d],[-w,0,-d],[-w,h,-d],[w,h,-d]] },
    { normal:[-1,0,0], corners:[[-w,0,-d],[-w,0,d],[-w,h,d],[-w,h,-d]] }
  ];
  let body = '';
  for (const face of faces) {
    const normal = rotate(face.normal);
    if (normal[0] + normal[2] < .01) continue;
    body += polygon(face.corners, shade(color, normal[0] > 0 ? -24 : -3));
    if (face.front) for (const center of [-.65,.65]) {
      const ring = Array.from({length:20},(_,i) => [center + .18 * Math.cos(i*Math.PI/10), .4 + .18 * Math.sin(i*Math.PI/10), d]);
      body += polygon(ring, shade(color, -58));
    }
  }
  body += polygon([[-w,h,-d],[w,h,-d],[w,h,d],[-w,h,d]], shade(color, 30));
  for (const x of [-.65,.65].sort((a,b) => { const ra=rotate([a,0,0]),rb=rotate([b,0,0]);return ra[0]+ra[2]-rb[0]-rb[2]; })) {
    const [cx,cy] = project([x,h+.13,0]);
    body += `<path d="M${cx-3.6} ${cy}v2.4a3.6 1.8 0 0 0 7.2 0v-2.4" fill="${color}"/><ellipse cx="${cx}" cy="${cy}" rx="3.6" ry="1.8" fill="${shade(color, x < 0 ? 3 : 38)}" stroke="${shade(color, -20)}" stroke-width=".4"/>`;
  }
  const marker = [[-.98,h+.01,-.47],[-.71,h+.01,-.47],[-.71,h+.01,-.17],[-.98,h+.01,-.17]];
  body += polygon(marker, shade(color, -50));
  return `<svg class="direction-model" viewBox="0 0 64 46" aria-hidden="true" focusable="false">${body}</svg>`;
}

export function rotationChoices(radians, action) {
  const angle = ((Math.round(radians * 180 / Math.PI) % 360) + 360) % 360;
  return `<div class="rotation-options" role="group" aria-label="积木方向">${[0,90,180,270].map(degrees => {
    const selected = angle === degrees;
    return `<button type="button" class="rotation-choice" data-action="${action}" data-angle="${degrees}" aria-label="方向 ${degrees}°" aria-pressed="${selected}">${directionModel(degrees,selected)}<span>${degrees}°</span></button>`;
  }).join('')}</div>`;
}
