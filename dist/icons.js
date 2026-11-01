/** Shared native SVG artwork for static controls and changing application states. */
export const ICON_ASSETS = Object.freeze({
  'magnifying-glass':'search', 'folder-open':'folder', 'paint-brush':'brush',
  wrench:'wrench', package:'box', 'floppy-disk':'notebook',
  heart:'heart', 'heart-filled':'heart-filled',
  cube:'cube', sparkle:'sparkle', plus:'add', 'upload-simple':'upload', 'cursor-click':'cursor',
  camera:'camera', image:'image', x:'close', check:'check',
  'arrow-right':'arrow-right', 'arrow-up-right':'arrow-up-right',
  'arrows-clockwise':'orbit', stack:'stack', explode:'explode',
  'magnifying-glass-plus':'zoom-in', 'magnifying-glass-minus':'zoom-out',
  'corners-out':'view-reset', 'hand-grabbing':'hand',
  'skip-back':'previous', 'skip-forward':'next', play:'play', pause:'pause',
  'arrow-counter-clockwise':'undo', 'arrow-clockwise':'redo', undo:'undo', redo:'redo',
  'rotate-brick':'rotate-brick', restart:'restart', restore:'restore',
  trash:'trash', 'check-circle':'success', info:'info', 'warning-circle':'warning',
  'download-simple':'download', 'list-bullets':'list'
});

function source(name) {
  if (!Object.hasOwn(ICON_ASSETS,name)) throw new Error(`Unknown UI icon: ${name}`);
  return `./assets/brick-${ICON_ASSETS[name]}.svg`;
}

export function icon(name) {
  return `<img class="icon brick-ui-icon" src="${source(name)}" data-icon="${name}" alt="" aria-hidden="true" draggable="false" width="24" height="24">`;
}

/** Change only the artwork, keeping the button, text and keyboard focus intact. */
export function updateIcon(container,name) {
  if (!container) return;
  const image=container.querySelector('img.brick-ui-icon');
  if (image?.dataset.icon===name) return;
  if (image) {
    image.src=source(name);
    image.dataset.icon=name;
  } else {
    container.insertAdjacentHTML('afterbegin',icon(name));
  }
}
