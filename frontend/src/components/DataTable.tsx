import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

interface Props<T> {
  data: T[];
  columns: ColumnDef<T, any>[];
  rowKey?: (row: T) => string | number;
  selectedKey?: string | number | null;
  onRowClick?: (row: T) => void;
  initialSort?: SortingState;
  // When provided, rows become collapsible accordions: clicking a row toggles
  // a full-width detail panel rendered beneath it (single-expand, auto-scroll).
  renderExpanded?: (row: T) => ReactNode;
  // Controlled open-row key. Omit to let the table own the state internally.
  expandedKey?: string | number | null;
  onExpandedChange?: (key: string | number | null) => void;
}

// Headless TanStack Table rendered with the app's dark .data-table styling.
export function DataTable<T>({
  data,
  columns,
  rowKey,
  selectedKey,
  onRowClick,
  initialSort = [],
  renderExpanded,
  expandedKey,
  onExpandedChange,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const expandable = !!renderExpanded;
  const controlled = expandedKey !== undefined;
  const [internalExpanded, setInternalExpanded] = useState<string | number | null>(null);
  const currentExpanded = controlled ? expandedKey! : internalExpanded;

  const expandedRowRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    if (currentExpanded != null && expandedRowRef.current) {
      expandedRowRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [currentExpanded]);

  const toggle = (key: string | number) => {
    const next = key === currentExpanded ? null : key;
    if (controlled) onExpandedChange?.(next);
    else setInternalExpanded(next);
  };

  const totalCols = table.getAllLeafColumns().length + (expandable ? 1 : 0);

  return (
    <table className="data-table">
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {expandable && <th aria-hidden style={{ width: 28 }} />}
            {hg.headers.map((h) => (
              <th key={h.id} onClick={h.column.getToggleSortingHandler()}>
                {flexRender(h.column.columnDef.header, h.getContext())}
                {{ asc: " ▲", desc: " ▼" }[h.column.getIsSorted() as string] ?? ""}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => {
          const key = rowKey ? rowKey(row.original) : row.id;
          const isOpen = expandable && key === currentExpanded;
          const highlighted = expandable
            ? isOpen
            : selectedKey != null && key === selectedKey;
          const handleClick = expandable
            ? () => toggle(key)
            : onRowClick
              ? () => onRowClick(row.original)
              : undefined;
          return (
            <Fragment key={key}>
              <tr
                ref={isOpen ? expandedRowRef : undefined}
                className={highlighted ? "selected" : ""}
                onClick={handleClick}
              >
                {expandable && (
                  <td className="expand-chevron">{isOpen ? "▼" : "▶"}</td>
                )}
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
              {isOpen && (
                <tr className="expanded-detail">
                  <td colSpan={totalCols}>{renderExpanded!(row.original)}</td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
