import React, { useEffect, useRef, useState } from 'react';

// Lazily-loaded mermaid instance. We don't import at module top-level
// because mermaid is ~600KB; lazy load keeps the bundle smaller for
// pages that don't render plans.
let mermaidPromise: Promise<any> | null = null;

async function getMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((m) => {
      const mermaid = m.default;
      mermaid.initialize({
        startOnLoad: false,
        theme: 'dark',
        securityLevel: 'loose',
        flowchart: { curve: 'basis', htmlLabels: true },
        themeVariables: {
          primaryColor: '#1a1a1a',
          primaryTextColor: '#d9d9d9',
          primaryBorderColor: '#69c0ff',
          lineColor: '#69c0ff',
          fontFamily: 'inherit',
        },
      });
      return mermaid;
    });
  }
  return mermaidPromise;
}

interface Props {
  source: string;
  id?: string;
}

const MermaidRenderer: React.FC<Props> = ({ source, id }) => {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    const render = async () => {
      if (!source || !ref.current) return;
      try {
        const mermaid = await getMermaid();
        const renderId = id || `mermaid-${Date.now()}`;
        const result = await mermaid.render(renderId, source);
        if (!cancelled) {
          setSvg(result.svg);
          setError('');
        }
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message || 'mermaid render failed');
          setSvg('');
        }
      }
    };
    render();
    return () => { cancelled = true; };
  }, [source, id]);

  if (error) {
    return (
      <div style={{ color: '#ff4d4f', fontSize: 11, padding: 8 }}>
        Mermaid render error: {error}
        <pre style={{ color: '#888', marginTop: 4 }}>{source}</pre>
      </div>
    );
  }

  return (
    <div
      ref={ref}
      style={{ overflow: 'auto', maxWidth: '100%' }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
};

export default MermaidRenderer;
