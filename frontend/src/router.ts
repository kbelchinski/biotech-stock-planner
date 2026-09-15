import { useEffect, useState } from "react";

export type Page = "screener" | "watchlist" | "research" | "plans" | "journal" | "integrations";

const PAGES: Page[] = ["screener", "watchlist", "research", "plans", "journal", "integrations"];

export interface Route {
  page: Page;
  param: string | null;
  query: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path, qs = ""] = raw.split("?");
  const [first, second] = path.split("/");
  const page = (PAGES as string[]).includes(first) ? (first as Page) : "screener";
  return { page, param: second ? decodeURIComponent(second) : null, query: new URLSearchParams(qs) };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parseHash(window.location.hash));
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const href = (page: Page, param?: string | null) => `#/${page}${param ? `/${encodeURIComponent(param)}` : ""}`;
