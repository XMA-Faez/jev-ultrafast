(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const composedParent = n => n.assignedSlot || n.parentElement || n.getRootNode().host || null;
  const composedClosest = (e,query) => {
    for (let n=e; n; n=composedParent(n)) if (n.matches(query)) return n;
    return null;
  };
  const shadowHosts = root => [...root.querySelectorAll('*')].filter(e=>e.shadowRoot);
  const queryComposed = (query,root=document) => {
    const found=[...root.querySelectorAll(query)], hosts=shadowHosts(root);
    if (!hosts.length) return found;
    const ordered=[]; let next=0;
    for (const host of hosts) {
      while (next<found.length && (found[next]===host ||
        found[next].compareDocumentPosition(host) & Node.DOCUMENT_POSITION_FOLLOWING)) ordered.push(found[next++]);
      ordered.push(...queryComposed(query,host.shadowRoot));
    }
    return ordered.concat(found.slice(next));
  };
  function* composedTextNodes(root) {
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
    let node;
    while ((node=walker.nextNode())) {
      if (node.nodeType===Node.TEXT_NODE) yield node;
      else if (node.shadowRoot) yield* composedTextNodes(node.shadowRoot);
    }
  }
  const visible = e => !composedClosest(e,'[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const byId = (e,id) => (e.getRootNode().getElementById?.(id)) || document.getElementById(id);
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(byId(e,id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton','slider'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel','date','time','month','color'].includes(e.type)) return 'textbox';
      if (e.type==='range') return 'slider';
    }
    return null;
  };
  const computedStyles=new Map();
  const styleOf = e => {
    if (!computedStyles.has(e)) computedStyles.set(e,getComputedStyle(e));
    return computedStyles.get(e);
  };
  const startsPointerCursor = e => {
    if (styleOf(e).cursor!=='pointer') return false;
    const parent=composedParent(e);
    return !parent || styleOf(parent).cursor!=='pointer';
  };
  const implicitlyClickable = e => {
    if (e===document.documentElement || e===document.body) return false;
    const focusable=e.hasAttribute('tabindex') && e.tabIndex>=0;
    if (!e.hasAttribute('onclick') && !(styleOf(e).cursor==='pointer' && (focusable || startsPointerCursor(e))))
      return false;
    if (e.tagName==='LABEL' && e.control && visible(e.control)) return false;
    const parent=composedParent(e);
    return !parent || !composedClosest(parent,selector);
  };
  const coveredByDescendantControl = (e,r) => queryComposed(selector,e).some(child => {
    const cr=child.getBoundingClientRect();
    return cr.width*cr.height>=0.8*r.width*r.height;
  });
  const ownVisibleText = e => [...composedTextNodes(e)].filter(node => {
    const parent=node.parentElement || node.parentNode?.host;
    return parent && !parent.closest('script,style,noscript,template') && !composedClosest(parent,selector) &&
      visible(parent);
  }).map(node=>node.textContent.trim()).filter(Boolean).join(' ');
  const implicitName = e => (e.getAttribute('aria-label') || ownVisibleText(e) || e.getAttribute('title') || '')
    .replace(/\s+/g,' ').trim().slice(0,120);
  const firstTextLine = (e,excluding='') => ((e.innerText||'').split('\n').map(line=>line.trim())
    .find(line=>line && line!==excluding) || '').slice(0,60);
  const scrollsVertically = e => e!==document.documentElement && e!==document.body && e.tagName!=='TEXTAREA' &&
    ['auto','scroll','overlay'].includes(styleOf(e).overflowY) && e.scrollHeight>e.clientHeight+2;
  const nativeFormat = e => {
    if (e.tagName!=='INPUT') return null;
    if (e.type==='range') return 'number '+(e.min||'0')+'..'+(e.max||'100')+' step '+(e.step||'1');
    return {date:'YYYY-MM-DD',time:'HH:MM',month:'YYYY-MM',color:'#rrggbb'}[e.type] || null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    queryComposed('input,textarea,select').filter(safe)
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  cache.toTop=(x,y,hitTest)=>{
    for (let view=window; view.frameElement; view=view.parent) {
      const f=view.frameElement, r=f.getBoundingClientRect(), style=f.ownerDocument.defaultView.getComputedStyle(f);
      x+=r.left+f.clientLeft+parseFloat(style.paddingLeft);
      y+=r.top+f.clientTop+parseFloat(style.paddingTop);
      if (!hitTest) continue;
      if (x<0 || y<0 || x>=view.parent.innerWidth || y>=view.parent.innerHeight) return null;
      let hit=view.parent.document.elementFromPoint(x,y);
      while (hit?.shadowRoot && hit!==f) {
        const inner=hit.shadowRoot.elementFromPoint(x,y);
        if (!inner || inner===hit) break;
        hit=inner;
      }
      if (hit!==f) return null;
    }
    return {x,y};
  };
  const frame_offset=window.frameElement ? cache.toTop(0,0,false) : null, offset=frame_offset || {x:0,y:0};
  const actions=[], scrollers=[];
  for (const e of queryComposed('*')) {
    if (scrollsVertically(e)) scrollers.push(e);
    const semantic=e.matches(selector);
    if (!semantic && !implicitlyClickable(e)) continue;
    if (!safe(e) || !visible(e) || e.matches(':disabled') || composedClosest(e,'[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=semantic ? role(e) : 'button';
    if (!rname || r.width<=0 || r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    const label=semantic ? name(e) : implicitName(e);
    if (!semantic && (!label || coveredByDescendantControl(e,r))) continue;
    const base={node:identity(e),role:rname,label:label||rname,
      rect:{x:r.x+offset.x,y:r.y+offset.y,w:r.width,h:r.height}};
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const format=nativeFormat(e);
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (format || ['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      const native=editable && format ? {native_value:true,format} : {};
      actions.push({...base,kind:editable?'fill':'click',value,...native});
      if (editable && !['range','color'].includes(e.type))
        actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  const nodesByLabel=new Map();
  for (const a of actions) if (a.kind!=='select') {
    if (!nodesByLabel.has(a.label)) nodesByLabel.set(a.label,new Set());
    nodesByLabel.get(a.label).add(a.node);
  }
  const surroundingItemText = (e,label,twins) => {
    for (let n=composedParent(e); n && n!==document.body; n=composedParent(n)) {
      if (twins.some(twin=>twin!==e && n.contains(twin))) return '';
      const line=firstTextLine(n,label);
      if (line) return line;
    }
    return '';
  };
  for (const a of actions) {
    const twins=a.kind==='select' ? null : nodesByLabel.get(a.label);
    if (!twins || twins.size<2) continue;
    const context=surroundingItemText(cache.nodes.get(a.node),a.label,[...twins].map(id=>cache.nodes.get(id)));
    if (context) a.label+=' ('+context+')';
  }
  const words=[], range=document.createRange(); let length=0;
  for (const node of composedTextNodes(document.body)) {
    if (length>=6000) break;
    const value=node.textContent.trim(), parent=node.parentElement || node.parentNode?.host;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  let focused=document.activeElement;
  while (focused?.shadowRoot?.activeElement) focused=focused.shadowRoot.activeElement;
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6],focused ? identity(focused) : null];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  const rootOverflow=styleOf(document.documentElement).overflowY;
  const viewportOverflow=rootOverflow==='visible' ? styleOf(document.body).overflowY : rootOverflow;
  const viewportScrolls=!['hidden','clip'].includes(viewportOverflow);
  if (viewportScrolls && scrollY+innerHeight<height-2)
    actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (viewportScrolls && scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  const visibleArea = e => {
    const r=e.getBoundingClientRect();
    const w=Math.min(r.right,innerWidth)-Math.max(r.left,0), h=Math.min(r.bottom,innerHeight)-Math.max(r.top,0);
    return w>=40 && h>=40 ? w*h : 0;
  };
  const scrollAreas=scrollers.filter(e=>visible(e) && visibleArea(e))
    .sort((a,b)=>visibleArea(b)-visibleArea(a)).slice(0,3);
  scrollAreas.forEach((e,i) => {
    const r=e.getBoundingClientRect(), center=(Math.max(r.left,0)+Math.min(r.right,innerWidth))/2;
    const side=center<innerWidth/3 ? 'left' : center>innerWidth*2/3 ? 'right' : 'middle';
    const heading=e.getAttribute('aria-label') || firstTextLine(e);
    const where='the '+side+' scrollable area'+(heading ? ' ("'+heading+'")' : '');
    const step=Math.max(100,Math.round(e.clientHeight*0.8)), container=identity(e);
    if (e.scrollTop+e.clientHeight<e.scrollHeight-2)
      actions.push({id:'scroll_down_in_'+(i+1),kind:'scroll',container,label:'Scroll down inside '+where,delta:step});
    if (e.scrollTop>0)
      actions.push({id:'scroll_up_in_'+(i+1),kind:'scroll',container,label:'Scroll up inside '+where,delta:-step});
  });
  actions.push({id:'press_enter',kind:'key',key:'Enter',label:'Press Enter in the focused field'},
    {id:'press_escape',kind:'key',key:'Escape',label:'Press Escape to close a menu or dialog'});
  const listFocused=focused && (['combobox','listbox','option'].includes(focused.getAttribute('role')) ||
    role(focused)==='combobox' || focused.getAttribute('aria-expanded')==='true');
  if (listFocused) actions.push({id:'arrow_down',kind:'key',key:'ArrowDown',label:'Press Arrow Down'},
    {id:'arrow_up',kind:'key',key:'ArrowUp',label:'Press Arrow Up'});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions,child_frames:window.length,
    ...(frame_offset && {frame_offset})};
})()
