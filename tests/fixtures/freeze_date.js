'use strict';

// Preload for node harnesses (`node --require freeze_date.js ...`): freezes
// Date.now() to FROZEN_NOW (ISO string) so date-cutoff extractors are
// deterministic over a fixture.
const frozen = Date.parse(process.env.FROZEN_NOW || '');
if (Number.isFinite(frozen)) Date.now = () => frozen;
