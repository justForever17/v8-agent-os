export type InboxOption = {
  id?: string;
  value?: string;
  title?: string;
  label?: string;
  detail?: string;
  description?: string;
};

export type InboxQuestion = {
  id?: string;
  title?: string;
  label?: string;
  question?: string;
  detail?: string;
  description?: string;
  multiSelect?: boolean;
  multiple?: boolean;
  options?: InboxOption[];
};

export type QuestionDraft = {
  selected: Record<string, string[]>;
  custom: Record<string, string>;
};

const text = (value: unknown) => typeof value === 'string' ? value.trim() : '';

export function optionKey(option: InboxOption, index: number) {
  return text(option.id) || text(option.value) || text(option.title) || text(option.label) || `option-${index + 1}`;
}

export function optionLabel(option: InboxOption, index: number) {
  return text(option.title) || text(option.label) || text(option.value) || `选项 ${index + 1}`;
}

export function optionDetail(option: InboxOption) {
  return text(option.detail) || text(option.description);
}

export function questionKey(question: InboxQuestion, index: number) {
  return text(question.id) || text(question.title) || text(question.question) || `q${index + 1}`;
}

export function questionTitle(question: InboxQuestion, index: number) {
  return text(question.title) || text(question.label) || text(question.question) || `问题 ${index + 1}`;
}

export function questionDetail(question: InboxQuestion) {
  return text(question.detail) || text(question.description);
}

export function normalizeQuestions(item: any): InboxQuestion[] {
  const request = item?.request && typeof item.request === 'object' ? item.request : item || {};
  const source = Array.isArray(request.questions) ? request.questions : [];
  const normalized = source.filter((q: any) => q && typeof q === 'object').map((q: any, index: number) => ({
    ...q,
    id: text(q.id) || `q${index + 1}`,
    title: text(q.title) || text(q.label) || text(q.question) || `问题 ${index + 1}`,
    detail: text(q.detail) || text(q.description),
    options: Array.isArray(q.options) ? q.options.filter((o: any) => o && typeof o === 'object') : [],
  }));
  if (normalized.length) return normalized;
  return [{
    id: 'answer',
    title: text(request.question) || text(request.prompt) || '回答问题',
    question: text(request.question) || text(request.prompt),
    detail: text(request.details),
    options: [],
  }];
}

export function createQuestionDraft(): QuestionDraft {
  return { selected: {}, custom: {} };
}

export function answerForQuestion(question: InboxQuestion, index: number, draft: QuestionDraft): string[] {
  const key = questionKey(question, index);
  const values = (draft.selected[key] || []).map((selected) => {
    const optionIndex = (question.options || []).findIndex((candidate, candidateIndex) => optionKey(candidate, candidateIndex) === selected);
    return optionIndex >= 0 ? optionLabel((question.options || [])[optionIndex], optionIndex) : selected;
  });
  const custom = text(draft.custom[key]);
  if (custom) values.push(custom);
  return values;
}

export function questionAnswered(question: InboxQuestion, index: number, draft: QuestionDraft) {
  return answerForQuestion(question, index, draft).length > 0;
}

/**
 * The Engine stores ask_user answers as one text value. Keep the same stable,
 * human-readable serialization used by Web and Phone; do not invent a second
 * permission payload or claim structured fields are understood by Engine.
 */
export function buildQuestionAnswer(questions: InboxQuestion[], draft: QuestionDraft) {
  return questions.map((question, index) => {
    const answer = answerForQuestion(question, index, draft);
    return answer.length ? `${index + 1}. ${questionTitle(question, index)}: ${answer.join('；')}` : '';
  }).filter(Boolean).join('\n').trim();
}

export function requestSummary(item: any) {
  const request = item?.request && typeof item.request === 'object' ? item.request : item || {};
  return {
    title: text(request.question) || text(request.prompt) || text(item?.question) || '回答问题',
    details: text(request.details),
  };
}
