import { Link, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useResearchDoc, useResearchList } from "../hooks/useResearch";

// The Lab's reading room: docs/research/* rendered in-app. The files in the
// repo are the primary artifact (versioned, LLM-readable); this page is a
// viewer, not an editor — new studies land here by adding a file. Markdown is
// rendered directly; .html docs (saved Claude artifact pages, self-contained
// by construction) keep their own styling inside a sandboxed iframe.

export function Research() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const list = useResearchList();
  const docs = list.data ?? [];
  const doc = useResearchDoc(slug);

  if (!slug) {
    return (
      <div className="research-index">
        <p className="muted" style={{ fontSize: 13 }}>
          Studies live in <code>docs/research/</code> in the repo; drop a markdown file there and
          it shows up here.
        </p>
        {list.isLoading && <div className="page-fallback" />}
        {docs.map((d) => (
          <Link key={d.slug} to={`/research/${d.slug}`} className="panel research-card">
            <div className="research-card-title">{d.title}</div>
            <div className="muted" style={{ fontSize: 12 }}>
              {d.date ?? ""}
              {d.date ? " · " : ""}
              {d.slug}.{d.kind}
              {d.kind === "html" ? " · artifact page" : ""}
            </div>
          </Link>
        ))}
        {!list.isLoading && docs.length === 0 && (
          <div className="panel muted">No research docs yet.</div>
        )}
      </div>
    );
  }

  return (
    <div className="research-doc">
      <div className="research-doc-nav">
        <Link to="/research" className="muted">
          ← All studies
        </Link>
        {docs.length > 1 && (
          <select
            value={slug}
            onChange={(e) => navigate(`/research/${e.target.value}`)}
            aria-label="Switch study"
          >
            {docs.map((d) => (
              <option key={d.slug} value={d.slug}>
                {d.title}
              </option>
            ))}
          </select>
        )}
      </div>
      {doc.isLoading && <div className="page-fallback" />}
      {doc.isError && <div className="panel neg">Couldn’t load this doc: {String(doc.error)}</div>}
      {doc.data?.kind === "md" && (
        <article className="panel md-body">
          <Markdown remarkPlugins={[remarkGfm]}>{doc.data.markdown ?? ""}</Markdown>
        </article>
      )}
      {doc.data?.kind === "html" && (
        <iframe
          className="research-frame"
          src={`/api/research/${doc.data.slug}/raw`}
          title={doc.data.title}
          sandbox="allow-scripts"
        />
      )}
    </div>
  );
}
