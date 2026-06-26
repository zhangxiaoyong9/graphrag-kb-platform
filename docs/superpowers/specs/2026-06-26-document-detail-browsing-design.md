# Document Detail Browsing Design

Date: 2026-06-26

## Summary

Add a document detail browsing experience that separates reading from graph analysis:

- The **document detail page** is optimized for reading the original document and verifying source evidence.
- A **right-side evidence drawer** opens when the user selects a citation/reference and shows the matched snippet plus a small amount of surrounding context.
- The **entity/relation browser** lives in a separate tab/page so extracted knowledge can be explored without interrupting document reading.

This design prioritizes source verification first, while still giving entity and relationship data a clear, dedicated surface.

## Goals

1. Let users open a document and read it without graph UI competing for attention.
2. Let users verify references quickly from the same page through a right-side drawer.
3. Let users browse extracted entities and relationships in a separate structured view.
4. Keep each page focused enough to test and extend independently.

## Non-goals

- This change does not redesign the global graph explorer.
- This change does not introduce PDF/Markdown/CSV export.
- This change does not require inline entity highlighting inside the document body.
- This change does not require full-screen graph visualization inside the document detail page.

## Current context

The KB platform is a React + TypeScript + Vite dashboard backed by a FastAPI server. Existing frontend surfaces include KB detail pages, document management, graph views, query views, jobs, and cost reporting. The API currently exposes document listing and graph/query endpoints; the implementation plan should verify whether document-body and per-document evidence endpoints already exist or need backend additions.

Relevant frontend areas:

- `web/src/pages/DocumentPage.tsx`
- `web/src/pages/DocumentsCenterPage.tsx`
- `web/src/pages/KbLayout.tsx`
- `web/src/components/DocumentManager.tsx`
- `web/src/components/GraphView.tsx`
- `web/src/api/client.ts`
- `web/src/api/types.ts`

## Recommended approach

Use **separate focused views**:

1. **Document detail page** for reading and evidence verification.
2. **Entity/relation tab** for structured extracted knowledge browsing.

This is preferred over embedding entity and relationship browsing directly inside the reading page because it preserves visual hierarchy, reduces layout crowding, and keeps future filtering/search improvements localized to the entity/relation surface.

## Page structure

### Document detail page

The document detail page is the primary reading surface.

It contains:

- Header with document title and metadata.
- Main content area with the document body.
- Citation/reference entry points.
- Right-side evidence drawer, closed by default.

The page should keep the document readable even when evidence data is unavailable. Evidence and reference failures should be local to the citation area or drawer.

### Evidence drawer

The evidence drawer opens on the right when a user clicks a citation/reference.

It shows:

- Matched snippet.
- One small context segment before the match.
- One small context segment after the match.
- Source metadata such as document title, chunk id, or source location when available.

Behavior:

- Selecting another citation replaces the drawer content instead of opening another drawer.
- Closing the drawer returns the page to the normal reading layout.
- Clicking blank document space does not close the drawer, to avoid accidental dismissal.
- Navigating to a different document closes and resets the drawer.

### Entity/relation browser

Entities and relationships should be presented in a dedicated tab/page rather than as the main document detail content.

The first version should favor a structured browser:

- Entity list or cards.
- Relationship list or cards.
- Click an entity to see related relationships.
- Click a relationship to navigate back to its connected entities.

The entity/relation browser can later grow filtering, sorting, and search without changing the reading page.

## Navigation model

From a document, users can choose between:

- **Reading / sources**: document body + citations + evidence drawer.
- **Entities / relationships**: separate tab/page for extracted graph data.

The two views should not be tightly coupled. If a future version adds cross-linking, it should be lightweight, such as a link from a citation or entity back to the relevant document/chunk.

## Interaction flow

### Citation verification

1. User opens a document detail page.
2. User clicks a citation/reference entry.
3. Right-side drawer opens.
4. Drawer shows matched snippet plus before/after context.
5. User clicks a different citation.
6. Drawer content updates in place.
7. User closes drawer or navigates away.

### Entity/relation browsing

1. User opens the entity/relation tab/page for the document or KB context.
2. User sees structured entities and relationships.
3. User selects an entity.
4. Related relationships are shown.
5. User selects a relationship.
6. Connected entities become visible or navigable.

## Empty states

### Document exists but indexing is not complete

- Document body remains readable if stored text is available.
- Citation and entity/relation sections show an "indexing in progress" message.
- Do not render an empty graph shell.

### No citations or evidence available

- Show a compact empty state in the citation area.
- Keep the document body visible.
- The drawer remains closed unless the user selects an available citation.

### No entities or relationships available

- The entity/relation page shows "No extracted entities or relationships yet.".
- Include a short explanation that indexing may still be running or no graph data was extracted.

## Error states

### Evidence load failure

- The drawer shows a local error message.
- The document page remains usable.
- The user can close the drawer or retry if retry is available.

### Missing context

- Show the matched snippet when possible.
- If before/after context is missing, label it as unavailable rather than failing the drawer.

### Entity/relation load failure

- Show a page-local error state.
- Preserve navigation back to the document reading view.

## Responsive behavior

Desktop:

- The evidence drawer appears as a right-side panel.
- The document content remains the dominant visual area.

Small screens:

- The evidence drawer can become a bottom sheet or full-screen overlay.
- The document body should not be permanently squeezed by the drawer.
- Entity/relation browsing can stack entities and relationships vertically.

## Data/API considerations

The implementation plan should inspect current backend capabilities before deciding exact endpoint changes.

Likely required data shapes:

- Document detail: id, title, metadata, body text or rendered text, indexing state.
- Citation/reference: id, label, snippet, source location, chunk id, optional score.
- Evidence detail: citation id, matched snippet, before context, after context, source metadata.
- Entity/relation data: entity id/name/type/description and relationship source/target/type/description/weight when available.

If the backend does not yet provide document-body or evidence-detail endpoints, add narrow endpoints rather than overloading existing graph/query endpoints.

## Testing plan

Frontend tests should cover:

- Rendering the document detail page with title, metadata, and body.
- Empty citation state.
- Clicking a citation opens the evidence drawer.
- Clicking another citation replaces drawer content.
- Closing the drawer hides it.
- Navigating to another document resets drawer state.
- Evidence load failure is local to the drawer.
- Entity/relation page renders empty, loading, success, and error states.
- Entity-to-relationship and relationship-to-entity navigation works.

Backend tests should cover any new endpoint behavior added during implementation:

- Document detail returns the expected stored text and metadata.
- Evidence detail returns matched snippet plus bounded before/after context.
- Missing evidence returns a clear 404 or empty response, depending on endpoint semantics.
- Entity/relation data can be fetched for the selected document or KB scope.

Manual verification should include:

- Desktop layout with drawer open and closed.
- Small-screen layout.
- Document with references.
- Document with no references.
- Indexed KB with entity/relationship data.
- New or partially indexed KB with empty extraction data.

## Rollout order

1. Confirm available document/evidence/entity data in current API and storage.
2. Add or extend the narrow API surfaces needed for document detail browsing.
3. Add frontend API types and client methods.
4. Build document detail page with citation list and evidence drawer.
5. Build entity/relation browser tab/page.
6. Add frontend and backend tests.
7. Run frontend build/tests and relevant backend tests.

## Open implementation questions

These should be answered during implementation planning, not by changing the product design:

1. Whether the entity/relation browser is scoped to a single document, an entire KB, or supports both.
2. Whether citations are derived from text units, query sources, graph extraction units, or a new document evidence model.
3. Whether document body rendering should preserve Markdown/HTML formatting or use plain text first.

The recommended first implementation should choose the smallest data source that already exists and can support the approved browsing experience without broad pipeline changes.
