import * as briefing from '../web/src/views/BriefingView';
import * as tasks from '../web/src/views/TasksView';
import * as approvals from '../web/src/views/ApprovalsView';
import * as workflows from '../web/src/views/WorkflowsView';
import * as agents from '../web/src/views/AgentsView';
import * as memory from '../web/src/views/MemoryView';
import * as research from '../web/src/views/ResearchView';
import * as finance from '../web/src/views/FinanceView';
import * as compliance from '../web/src/views/ComplianceView';
import * as security from '../web/src/views/SecurityView';
import * as chat from '../web/src/views/ChatView';

const views = {
  default: {
    Briefing: briefing.default,
    Tasks: tasks.default,
    Approvals: approvals.default,
    Workflows: workflows.default,
    Agents: agents.default,
    Memory: memory.default,
    Research: research.default,
    Finance: finance.default,
    Compliance: compliance.default,
    Security: security.default,
    Chat: chat.default,
  },
};

export { views };
