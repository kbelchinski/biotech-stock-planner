import { useEffect, useRef, useState } from "react";
import { Bell, BookOpen, Calculator, Eye, FlaskConical, Microscope, PlugZap, type LucideIcon } from "lucide-react";
import { errorMessage, researchApi } from "../api";
import { dateTime } from "../format";
import { href, type Page, type Route } from "../router";
import type { Notification } from "../researchTypes";

const LINKS: { page: Page; label: string; icon: LucideIcon }[] = [
  { page: "screener", label: "Screener", icon: FlaskConical },
  { page: "watchlist", label: "Watchlist", icon: Eye },
  { page: "research", label: "Company research", icon: Microscope },
  { page: "plans", label: "Trade plans", icon: Calculator },
  { page: "journal", label: "Journal & performance", icon: BookOpen },
  { page: "integrations", label: "Data & integrations", icon: PlugZap },
];

interface Props {
  route: Route;
  unread: number;
  onUnreadChange: (count: number) => void;
}

export function NavBar({ route, unread, onUnreadChange }: Props) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    setError(null);
    try {
      const data = await researchApi.notifications();
      setItems(data.items);
      onUnreadChange(data.unread);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  useEffect(() => {
    if (!open) return;
    void load();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const onClick = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const markAll = async () => {
    try {
      const data = await researchApi.markRead(null);
      onUnreadChange(data.unread);
      setItems((current) => current?.map((n) => ({ ...n, read: true })) ?? null);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <nav className="navbar" aria-label="Main">
      <ul className="nav-links">
        {LINKS.map(({ page, label, icon: Icon }) => (
          <li key={page}>
            <a href={href(page)} className="nav-link" aria-current={route.page === page ? "page" : undefined}>
              <Icon aria-hidden size={15} />
              <span>{label}</span>
            </a>
          </li>
        ))}
      </ul>
      <div className="nav-bell" ref={wrapRef}>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-expanded={open}
          aria-controls="notification-feed"
          onClick={() => setOpen((v) => !v)}
          aria-label={`Notifications, ${unread} unread`}
        >
          <Bell aria-hidden size={16} />
          {unread > 0 && <span className="bell-count">{unread > 99 ? "99+" : unread}</span>}
        </button>
        {open && (
          <div className="popover" id="notification-feed" role="dialog" aria-label="Notifications">
            <div className="popover-head">
              <strong>Notifications</strong>
              <button type="button" className="link" onClick={markAll} disabled={!items?.some((n) => !n.read)}>
                Mark all read
              </button>
            </div>
            <p className="popover-note">In-app only. Checks run only while the backend is running.</p>
            {error && <p className="notes-error">{error}</p>}
            {items === null && !error && <p className="muted popover-empty">Loading…</p>}
            {items && items.length === 0 && <p className="muted popover-empty">No notifications yet. Watch companies and refresh to track changes.</p>}
            {items && items.length > 0 && (
              <ul className="feed">
                {items.map((n) => (
                  <li key={n.id} className={n.read ? "" : "is-unread"}>
                    <a href={href("research", n.ticker)} onClick={() => setOpen(false)}>
                      <span className={`feed-kind feed-${n.kind}`}>{n.kind.replaceAll("_", " ")}</span>
                      <strong>{n.title}</strong>
                      <span className="feed-body">{n.body}</span>
                      <span className="feed-time">{dateTime(n.created_at)}</span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </nav>
  );
}
