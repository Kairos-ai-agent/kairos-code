import React, { useEffect, useRef, useState } from 'react';

// Lazily-loaded mermaid instance. We don't bundle mermaid (~600KB) —
// instead we inject a CDN <script> on first use and resolve when its
// global `mermaid` becomes available. This keeps the main bundle small
// for pages that never render a plan diagram.
const MERMAID_VERSION = '10.9.1';
const MERMAID_CDN = `https://cdn.jsdelivr.net/npm/mermaid@${MERMAID_VERSION}/dist/mermaid.min.js`;

let mermaidPromise: Promise<any> | null = null;

function loadMermaidFromCdn(): Promise<any> {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('mermaid requires a browser environment'));
  }
  if ((window as any).mermaid) {
    return Promise.resolve((window as any).mermaid);
  }
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(
      `script[data-mermaid-loader="1"]`,
    ) as HTMLScriptElement | null;
    const onReady = () => {
      const mermaid = (window as any).mermaid;
      if (!mermaid) {
        reject(new Error('mermaid script loaded but global is missing'));
        return;
      }
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
      resolve(mermaid);
    };
    if (existing) {
      existing.addEventListener('load', onReady);
      existing.addEventListener('error', () =>
        reject(new Error('mermaid CDN script failed to load')),
      );
      // If the script already finished before we attached, resolve now.
      if ((window as any).mermaid) onReady();
      return;
    }
    const script = document.createElement('script');
    script.src = MERMAID_CDN;
    script.async = true;
    script.dataset.mermaidLoader = '1';
    script.addEventListener('load', onReady);
    script.addEventListener('error', () =>
      reject(new Error('mermaid CDN script failed to load')),
    );
    document.head.appendChild(script);
  });
}

async function getMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = loadMermaidFromCdn();
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