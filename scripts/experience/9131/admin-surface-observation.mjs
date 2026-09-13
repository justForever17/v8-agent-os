// Read-only DOM measurements shared by the Admin and Extensions browser probes.
export function measureAdminSurface() {
  const app = document.querySelector('.admin-app');
  const visible = element => element && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
  const box = element => {
    if (!visible(element)) return null;
    const bounds = element.getBoundingClientRect(), style = getComputedStyle(element);
    return {x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height,color:style.color,background:style.backgroundColor,
      borderColor:style.borderColor,radius:style.borderRadius,fontFamily:style.fontFamily,fontSize:style.fontSize,lineHeight:style.lineHeight};
  };
  const style = getComputedStyle(app || document.documentElement);
  const properties = ['--v8-product-topbar-height','--v8-product-motion-fast','--v8-product-motion-control','--v8-product-motion-panel',
    '--v8-product-panel-radius','--v8-product-control-radius','--v8-product-settings-width'];
  return {
    theme:document.documentElement.className,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
    app:box(app),topbar:box(document.querySelector('.v8-product-topbar')),content:box(document.querySelector('.admin-content')),
    page:box(document.querySelector('.admin-page')),heading:box(document.querySelector('h1')),
    headings:[...document.querySelectorAll('h1')].filter(visible).map(element=>element.textContent.trim()),
    sidebar:box(document.querySelector('aside')),saveHost:box(document.querySelector('#admin-save-actions')),
    activeNavigation:[...document.querySelectorAll('nav a[aria-current="page"]')].filter(visible).map(element=>({text:element.textContent.trim(),href:element.getAttribute('href'),...box(element)})),
    tokens:Object.fromEntries(properties.map(property=>[property,style.getPropertyValue(property).trim()])),
    horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
    stylesheets:[...document.querySelectorAll('link[rel="stylesheet"]')].map(element=>new URL(element.href).pathname),
  };
}
