"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import { useControlQuery } from "@/lib/api/use-control-query";


type HostCollection = { items?: Array<{ id?: string }> };
type Capabilities = {
  status?: string;
  features?: Record<string, { available?: boolean; status?: string }>;
};

const destinations = [
  { href: "/inbox", label: "Inbox", icon: "inbox" },
  { href: "/runs", label: "Runs", icon: "runs" },
  { href: "/changes", label: "Changes", icon: "changes" },
  { href: "/verification", label: "Verification", icon: "verification" },
  { href: "/hosts", label: "Hosts & integrations", shortLabel: "Hosts", icon: "hosts" },
] as const;

export function ConsoleShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const menu = useRef<HTMLDialogElement>(null);
  const hosts = useControlQuery<HostCollection>("/v1/hosts");
  const hostId = hosts.data?.items?.[0]?.id;
  const capabilities = useControlQuery<Capabilities>(
    `/v1/capabilities?host_id=${encodeURIComponent(hostId ?? "")}`,
    { enabled: Boolean(hostId) },
  );

  useEffect(() => {
    menu.current?.close();
  }, [pathname]);

  const health = capabilities.data?.status ?? (hosts.isPending ? "loading" : hostId ? "unknown" : "setup");

  return (
    <div className="console-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="mobile-app-bar">
        <Brand />
        <button
          aria-label="Open navigation"
          className="icon-button mobile-menu-button"
          onClick={() => menu.current?.showModal()}
          type="button"
        >
          <MenuIcon />
        </button>
      </header>
      <aside aria-label="Primary" className="console-sidebar">
        <Brand />
        <PrimaryNavigation pathname={pathname} />
        <div className="sidebar-health">
          <span aria-hidden="true" className="sidebar-health__dot" data-health={health} />
          <span>{healthLabel(health)}</span>
        </div>
      </aside>
      <dialog className="navigation-sheet" ref={menu}>
        <div className="navigation-sheet__header">
          <Brand />
          <button
            aria-label="Close navigation"
            className="icon-button"
            onClick={() => menu.current?.close()}
            type="button"
          >
            <CloseIcon />
          </button>
        </div>
        <PrimaryNavigation pathname={pathname} />
      </dialog>
      <main id="main-content">{children}</main>
    </div>
  );
}

function PrimaryNavigation({ pathname }: { pathname: string }) {
  return (
    <nav aria-label="Control console" className="primary-navigation">
      {destinations.map((destination) => {
        const current = pathname === destination.href || pathname.startsWith(`${destination.href}/`);
        return (
          <Link
            aria-current={current ? "page" : undefined}
            className="destination-link"
            data-current={current || undefined}
            href={destination.href}
            key={destination.href}
            title={destination.label}
          >
            <NavigationIcon name={destination.icon} />
            <span className="destination-link__label">{destination.label}</span>
            <span className="destination-link__short-label">
              {"shortLabel" in destination ? destination.shortLabel : destination.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <Link aria-label="LoopGuard inbox" className="brand" href="/inbox">
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 3.2l7 3.1v5.2c0 4.5-2.7 7.5-7 9.3-4.3-1.8-7-4.8-7-9.3V6.3l7-3.1z" />
        <path d="M9 12.1l2 2 4-4.2" />
      </svg>
      <span>LoopGuard</span>
    </Link>
  );
}

function NavigationIcon({ name }: { name: (typeof destinations)[number]["icon"] }) {
  const paths = {
    inbox: <path d="M3 4.5h14v11H3zM3 11h4l1.5 2h3l1.5-2h4" />,
    runs: <path d="M4 3.5h12v13H4zM7 7h6M7 10h6M7 13h3" />,
    changes: <path d="M6 3v10.5a2.5 2.5 0 005 0V6m-2 2l2-2 2 2M6 6H3m3 4H3" />,
    verification: <path d="M10 2.8l6 2.7v4.4c0 3.8-2.3 6.4-6 7.9-3.7-1.5-6-4.1-6-7.9V5.5l6-2.7zM7 10l2 2 4-4" />,
    hosts: <path d="M3 4h14v9H3zM7 16h6M10 13v3M5.5 7h.1M8 7h6" />,
  };
  return (
    <svg aria-hidden="true" className="navigation-icon" viewBox="0 0 20 20">
      {paths[name]}
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="M3 5h14M3 10h14M3 15h14" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20">
      <path d="M4 4l12 12M16 4L4 16" />
    </svg>
  );
}

function healthLabel(health: string): string {
  if (health === "ready") return "Control API current";
  if (health === "degraded") return "Host data degraded";
  if (health === "loading") return "Checking host status";
  if (health === "setup") return "Host setup required";
  return "Host status unknown";
}
