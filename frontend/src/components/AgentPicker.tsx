import { useEffect, useId, useRef, useState } from 'react';
import { AgentIcon } from './AgentIcon';
import { Icon } from './Icon';
import { agents } from './usageLabels';

const options = [{ id: 'all', name: 'All agents' }, ...agents];

export function AgentPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const typed = useRef({ text: '', at: 0 });
  const selected = Math.max(0, options.findIndex(option => option.id === value));
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [open]);
  function choose(index: number) { onChange(options[index].id); setOpen(false); }
  return <div className="agent-picker" ref={root}>
    <button type="button" role="combobox" aria-label="Agent" aria-expanded={open} aria-haspopup="listbox" aria-controls={`${id}-list`} aria-activedescendant={open ? `${id}-${active}` : undefined} className="agent-picker__trigger" onClick={() => { setActive(selected); setOpen(!open); }} onKeyDown={event => {
      if (event.key === 'Tab') { setOpen(false); return; }
      if (event.key === 'Escape') { setOpen(false); event.preventDefault(); return; }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        setActive(event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : !open ? selected : (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
        setOpen(true); return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (open) choose(active); else { setActive(selected); setOpen(true); }
        return;
      }
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        typed.current = { text: (Date.now() - typed.current.at < 700 ? typed.current.text : '') + event.key.toLowerCase(), at: Date.now() };
        const match = options.findIndex(option => option.name.toLowerCase().startsWith(typed.current.text));
        if (match >= 0) { event.preventDefault(); setActive(match); setOpen(true); }
      }
    }}>
      {value === 'all' ? <Icon name="layers" /> : <AgentIcon provider={value} />}<span>{options[selected].name}</span><Icon name="chevron" />
    </button>
    {open ? <ul id={`${id}-list`} role="listbox" aria-label="Agents" className="agent-picker__menu">
      {options.map((option, index) => <li id={`${id}-${index}`} key={option.id} role="option" aria-selected={value === option.id} className={active === index ? 'is-active' : ''} onMouseEnter={() => setActive(index)} onMouseDown={event => event.preventDefault()} onClick={() => choose(index)}>
        {option.id === 'all' ? <Icon name="layers" /> : <AgentIcon provider={option.id} />}<span>{option.name}</span>{value === option.id ? <Icon name="check" /> : null}
      </li>)}
    </ul> : null}
  </div>;
}
