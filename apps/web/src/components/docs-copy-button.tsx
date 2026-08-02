"use client";

import { useState } from "react";


export function DocsCopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }

  return (
    <button className="docs-copy" onClick={copy} type="button">
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
