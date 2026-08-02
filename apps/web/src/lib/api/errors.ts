export type ProblemDocument = {
  type?: string;
  code?: string;
  title?: string;
  detail?: string;
  request_id?: string;
  retryable?: boolean;
  doc_url?: string;
  field?: string;
  current_state?: unknown;
};

export class ControlProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly retryable: boolean;
  readonly docUrl?: string;
  readonly field?: string;
  readonly currentState?: unknown;

  constructor(status: number, problem: ProblemDocument) {
    super(problem.detail ?? problem.title ?? `Control API request failed with HTTP ${status}`);
    this.name = "ControlProblem";
    this.status = status;
    this.code = problem.code ?? "unknown_problem";
    this.requestId = problem.request_id;
    this.retryable = problem.retryable ?? false;
    this.docUrl = problem.doc_url ?? problem.type;
    this.field = problem.field;
    this.currentState = problem.current_state;
  }
}

export async function decodeResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  let problem: ProblemDocument = {};
  try {
    problem = (await response.json()) as ProblemDocument;
  } catch {
    // The bounded fallback below prevents an upstream HTML error page from leaking into UI copy.
  }
  throw new ControlProblem(response.status, problem);
}
