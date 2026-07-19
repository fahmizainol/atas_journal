import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { qk } from "../lib/queryKeys";

export interface ResearchDocMeta {
  slug: string;
  kind: "md" | "html";
  title: string;
  date: string | null;
  mtime: number;
}

export interface ResearchDoc {
  slug: string;
  kind: "md" | "html";
  title: string;
  markdown: string | null;
}

export function useResearchList() {
  return useQuery({
    queryKey: qk.researchList,
    queryFn: () => apiGet<ResearchDocMeta[]>("/research"),
    staleTime: 30_000,
  });
}

export function useResearchDoc(slug: string | undefined) {
  return useQuery({
    queryKey: qk.researchDoc(slug ?? ""),
    queryFn: () => apiGet<ResearchDoc>(`/research/${slug}`),
    enabled: !!slug,
    staleTime: 30_000,
  });
}
