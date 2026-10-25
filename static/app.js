const $ = id => document.getElementById(id);
const selected = new Map();
let visibleTasks = [];
let taskFields = [];
let pairs = [];
let showSelected = false;
let searchQuery = '';
let tasksLoading = false;
let fieldsLoading = false;
let fieldsError = false;
let requestVersion = 0;
let fieldsRequestVersion = 0;
let fieldsTimer;
let buildingSchema = false;
let schemaRevision = 0;
let generatingAnalysis = false;
let analysisRevision = 0;

function currentTasks() {
  return showSelected ? [...selected.values()].filter(task => task.task_name.includes(searchQuery)) : visibleTasks;
}

function renderSelection() {
  $('selected-count').textContent = selected.size;
  $('clear').disabled = selected.size === 0;
  $('config-summary').textContent = selected.size ? `已选 ${selected.size} 个任务 · ${pairs.length} 组关联` : '未选择任务';
  syncSelectAll();
  syncRelationState();
}

function render() {
  $('tasks').replaceChildren();
  const tasks = currentTasks();
  for (const task of tasks) {
    const row = document.createElement('label');
    row.className = 'task-option';
    row.classList.toggle('selected', selected.has(task.report_task_no));
    row.title = task.report_task_no;
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = selected.has(task.report_task_no);
    checkbox.setAttribute('aria-label', `选择${task.task_name}`);
    checkbox.onchange = () => {
      if (checkbox.checked) selected.set(task.report_task_no, task);
      else selected.delete(task.report_task_no);
      row.classList.toggle('selected', checkbox.checked);
      scheduleFields();
      if (showSelected) render();
    };
    const copy = document.createElement('span');
    copy.className = 'task-copy';
    const name = document.createElement('span');
    name.className = 'task-name';
    name.textContent = task.task_name;
    const meta = document.createElement('small');
    meta.className = 'task-meta';
    meta.textContent = `${task.data_time_value} · ${task.publish_org_name || '未提供组织'}`;
    copy.append(name, meta);
    row.append(checkbox, copy);
    $('tasks').append(row);
  }
  const loading = tasksLoading && !showSelected;
  $('loading').hidden = !loading;
  $('empty').hidden = tasks.length > 0 || loading || !$('load-error').hidden;
  $('result-count').textContent = loading ? '加载中…' : `${tasks.length} 项结果`;
  renderSelection();
}

function syncSelectAll() {
  const tasks = currentTasks();
  const count = tasks.filter(task => selected.has(task.report_task_no)).length;
  $('select-all').disabled = tasks.length === 0;
  $('select-all').checked = tasks.length > 0 && count === tasks.length;
  $('select-all').indeterminate = count > 0 && count < tasks.length;
}

async function loadTasks() {
  const version = ++requestVersion;
  searchQuery = $('search').value.trim();
  tasksLoading = true;
  visibleTasks = [];
  $('load-error').hidden = true;
  render();
  try {
    const response = await fetch(`/api/tasks?q=${encodeURIComponent(searchQuery)}`);
    if (!response.ok) throw new Error('数据库暂时不可用，请检查配置后重试。');
    const data = await response.json();
    if (version !== requestVersion) return;
    visibleTasks = data.tasks;
    $('environment').textContent = data.environment === 'development' ? '本地测试数据' : '已配置数据库';
    $('task-total').textContent = data.total;
  } catch (error) {
    if (version !== requestVersion) return;
    $('error-text').textContent = error instanceof TypeError ? '连接失败，请确认本地服务已启动。' : error.message;
    $('load-error').hidden = false;
    $('task-total').textContent = '—';
    $('environment').textContent = '连接异常';
  } finally {
    if (version === requestVersion) {
      tasksLoading = false;
      render();
    }
  }
}

function setTaskFilter(onlySelected) {
  showSelected = onlySelected;
  $('all-filter').classList.toggle('active', !onlySelected);
  $('selected-filter').classList.toggle('active', onlySelected);
  $('all-filter').setAttribute('aria-pressed', String(!onlySelected));
  $('selected-filter').setAttribute('aria-pressed', String(onlySelected));
  render();
}

function selectionMatches(taskNos) {
  return taskNos.length === selected.size && taskNos.every(taskNo => selected.has(taskNo));
}

function syncRelationState() {
  const ready = selected.size > 0 && !fieldsLoading && !fieldsError && selectionMatches(taskFields.map(task => task.report_task_no));
  $('relation-editor').hidden = selected.size === 0 || taskFields.length === 0;
  $('relation-editor').disabled = !ready;
  $('confirm-relations').disabled = !ready || buildingSchema || generatingAnalysis;
  $('confirm-relations').textContent = generatingAnalysis ? '模型生成中…' : buildingSchema ? '正在生成…' : !$('analysis-view').hidden ? '生成分析 SQL' : '生成宽表表头';
  $('relations-start').hidden = selected.size > 0;
  $('fields-status').textContent = fieldsError ? '字段读取失败' : fieldsLoading ? '正在加载字段…' : ready ? '字段已就绪' : '尚未选择任务';
  $('retry-fields').hidden = !fieldsError;
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : data.detail;
    throw new Error(detail || '请求失败，请重试。');
  }
  return data;
}

function scheduleFields() {
  clearTimeout(fieldsTimer);
  const version = ++fieldsRequestVersion;
  pairs = pairs.filter(pair => selected.has(pair.left_task_no) && selected.has(pair.right_task_no));
  taskFields = taskFields.filter(task => selected.has(task.report_task_no));
  fieldsError = false;
  fieldsLoading = selected.size > 0;
  $('fields-notice').hidden = true;
  renderPairs();
  renderSelection();
  if (selected.size) fieldsTimer = setTimeout(() => loadFields(version), 150);
}

async function loadFields(version) {
  const taskNos = [...selected.keys()];
  try {
    const data = await postJSON('/api/fields', {report_task_nos: taskNos});
    if (version !== fieldsRequestVersion) return;
    taskFields = data.tasks;
    const withoutFields = taskFields.filter(task => task.fields.length === 0);
    $('fields-notice').hidden = withoutFields.length === 0;
    $('fields-notice').textContent = withoutFields.length ? `暂无可用历史字段：${withoutFields.map(task => task.task_name).join('、')}。这些任务暂不能添加字段关联。` : '';
    fillTaskOptions();
    renderPairs();
  } catch (error) {
    if (version !== fieldsRequestVersion) return;
    fieldsError = true;
    $('fields-notice').textContent = error instanceof TypeError ? '字段加载失败，请重试。' : error.message;
    $('fields-notice').hidden = false;
  } finally {
    if (version === fieldsRequestVersion) {
      fieldsLoading = false;
      syncRelationState();
    }
  }
}

function fillTaskOptions() {
  for (const id of ['pair-left', 'pair-right']) {
    const value = $(id).value;
    $(id).replaceChildren(new Option('请选择任务', ''));
    for (const task of taskFields) {
      const option = new Option(`${task.task_name}${task.fields.length ? '' : '（无历史字段）'}`, task.report_task_no);
      option.disabled = task.fields.length === 0;
      $(id).append(option);
    }
    $(id).value = taskFields.some(task => task.report_task_no === value && task.fields.length) ? value : '';
  }
  $('single-task').hidden = taskFields.length !== 1;
  $('pair-picker').disabled = taskFields.length < 2;
}

function relationDraft() {
  return {report_task_nos: [...selected.keys()], pairs};
}

function invalidateRelations() {
  invalidateSchema();
  $('relation-confirmation').hidden = true;
  $('pair-error').hidden = true;
  $('relation-payload-details').hidden = selected.size === 0;
  $('relation-payload').textContent = JSON.stringify(relationDraft(), null, 2);
  $('pair-count').textContent = `${pairs.length} 组`;
  $('pairs-empty').hidden = pairs.length > 0 || taskFields.length === 1;
  $('config-summary').textContent = selected.size ? `已选 ${selected.size} 个任务 · ${pairs.length} 组关联` : '未选择任务';
}

function fillFieldOptions(select, task, value, label) {
  select.setAttribute('aria-label', label);
  select.replaceChildren(new Option('请选择字段', ''));
  for (const field of task.fields) {
    const names = [...new Set(field.definitions.map(definition => definition.field_name))].join(' / ');
    const option = new Option(`${names} [${field.field_code}]`, field.field_code);
    option.title = field.definitions.map(definition => `${definition.report_template_no}：${definition.field_name} · ${definition.data_type}`).join('\n');
    select.append(option);
  }
  if (value && !task.fields.some(field => field.field_code === value)) {
    select.append(new Option(`${value}（字段已失效，请重新选择）`, value));
  }
  select.value = value;
}

function renderPairs() {
  $('pairs').replaceChildren();
  pairs.forEach((pair, pairIndex) => {
    const number = pairIndex + 1;
    const left = taskFields.find(task => task.report_task_no === pair.left_task_no);
    const right = taskFields.find(task => task.report_task_no === pair.right_task_no);
    const card = $('pair-template').content.firstElementChild.cloneNode(true);
    card.querySelector('.pair-title').textContent = `关联组 ${number} · ${pair.links.length} 条条件`;
    card.querySelector('.left-name').textContent = left.task_name;
    card.querySelector('.right-name').textContent = right.task_name;
    const join = card.querySelector('.join-type');
    const joinNote = card.querySelector('.join-note');
    const updateJoinNote = () => {
      joinNote.textContent = pair.join_type === 'LEFT' ? `保留「${left.task_name}」全部行，右侧未匹配字段为空。` : '只保留两侧字段条件全部匹配的行。';
    };
    join.value = pair.join_type;
    join.setAttribute('aria-label', `关联组 ${number} 连接方式`);
    join.onchange = () => { pair.join_type = join.value; updateJoinNote(); invalidateRelations(); };
    updateJoinNote();
    const removePair = card.querySelector('.delete-pair');
    removePair.setAttribute('aria-label', `删除关联组 ${number}`);
    removePair.onclick = () => { pairs.splice(pairIndex, 1); renderPairs(); };
    const swap = card.querySelector('.swap-pair');
    swap.setAttribute('aria-label', `交换关联组 ${number} 左右任务`);
    swap.onclick = () => {
      [pair.left_task_no, pair.right_task_no] = [pair.right_task_no, pair.left_task_no];
      for (const link of pair.links) [link.left_field_code, link.right_field_code] = [link.right_field_code, link.left_field_code];
      renderPairs();
    };
    pair.links.forEach((link, linkIndex) => {
      const row = $('link-template').content.firstElementChild.cloneNode(true);
      row.querySelector('.condition-number').textContent = linkIndex ? 'AND' : '条件';
      const leftField = row.querySelector('.left-field');
      const rightField = row.querySelector('.right-field');
      fillFieldOptions(leftField, left, link.left_field_code, `关联组 ${number} 条件 ${linkIndex + 1} 左侧字段`);
      fillFieldOptions(rightField, right, link.right_field_code, `关联组 ${number} 条件 ${linkIndex + 1} 右侧字段`);
      leftField.onchange = () => { link.left_field_code = leftField.value; invalidateRelations(); };
      rightField.onchange = () => { link.right_field_code = rightField.value; invalidateRelations(); };
      const removeLink = row.querySelector('.delete-link');
      removeLink.setAttribute('aria-label', `删除关联组 ${number} 条件 ${linkIndex + 1}`);
      removeLink.onclick = () => { pair.links.splice(linkIndex, 1); renderPairs(); };
      card.querySelector('.field-links').append(row);
    });
    const addLink = card.querySelector('.add-link');
    addLink.setAttribute('aria-label', `关联组 ${number} 添加字段条件`);
    addLink.onclick = () => { pair.links.push({left_field_code: '', right_field_code: ''}); renderPairs(); };
    $('pairs').append(card);
  });
  invalidateRelations();
}

$('pair-form').onsubmit = event => {
  event.preventDefault();
  const left = $('pair-left').value;
  const right = $('pair-right').value;
  let error = '';
  if (!left || !right) error = '请先选择左右任务。';
  else if (left === right) error = '请选择两个不同的任务。';
  else if (pairs.some(pair => [pair.left_task_no, pair.right_task_no].includes(left) && [pair.left_task_no, pair.right_task_no].includes(right))) error = '这对任务已有一组关联，请在已有组内添加字段条件。';
  if (error) {
    $('pair-error').textContent = error;
    $('pair-error').hidden = false;
    return;
  }
  pairs.push({left_task_no: left, right_task_no: right, join_type: $('pair-type').value,
    links: [{left_field_code: '', right_field_code: ''}]});
  renderPairs();
};

async function generateSchema() {
  if (buildingSchema || $('confirm-relations').disabled) return;
  $('relation-confirmation').hidden = true;
  const incomplete = pairs.findIndex(pair => !pair.links.length || pair.links.some(link => !link.left_field_code || !link.right_field_code));
  if (incomplete >= 0) {
    $('pair-error').textContent = `关联组 ${incomplete + 1}：请补充完整字段条件，或删除整组。`;
    $('pair-error').hidden = false;
    return;
  }
  invalidateSchema();
  const revision = schemaRevision;
  const snapshot = JSON.stringify(relationDraft());
  buildingSchema = true;
  $('confirm-relations').textContent = '正在生成…';
  syncRelationState();
  try {
    const data = await postJSON('/api/schema', JSON.parse(snapshot));
    if (revision !== schemaRevision || snapshot !== JSON.stringify(relationDraft()) || !selectionMatches(taskFields.map(task => task.report_task_no)) || fieldsLoading || fieldsError) return;
    $('pair-error').hidden = true;
    renderSchema(data);
    $('relation-confirmation').textContent = `已生成 ${data.columns.length} 列表头。`;
    $('relation-confirmation').hidden = false;
  } catch (error) {
    if (revision !== schemaRevision || snapshot !== JSON.stringify(relationDraft())) return;
    $('pair-error').textContent = error instanceof TypeError ? '连接失败，请重试。' : error.message;
    $('pair-error').hidden = false;
  } finally {
    buildingSchema = false;
    $('confirm-relations').textContent = '生成宽表表头';
    syncRelationState();
  }
}

function showSchema(show) {
  showView(show ? 'schema' : 'relations');
}

function showView(name) {
  if ($(name + '-tab').disabled) return;
  for (const view of ['relations', 'schema', 'analysis']) {
    const active = view === name;
    $(view + '-view').hidden = !active;
    $(view + '-tab').classList.toggle('active', active);
    $(view + '-tab').setAttribute('aria-selected', String(active));
    $(view + '-tab').tabIndex = active ? 0 : -1;
  }
  syncRelationState();
}

function invalidateSchema() {
  schemaRevision++;
  invalidateAnalysis();
  $('analysis-tab').disabled = true;
  $('schema-tab').disabled = true;
  $('schema-count').hidden = true;
  $('schema-rows').replaceChildren();
  $('schema-json').textContent = '';
  $('schema-summary').textContent = '';
  $('relation-confirmation').hidden = true;
  showSchema(false);
}

function renderSchema(data) {
  $('schema-rows').replaceChildren();
  for (const column of data.columns) {
    const row = document.createElement('tr');
    const nameCell = document.createElement('td');
    const name = document.createElement('strong');
    name.textContent = column.column_name;
    const id = document.createElement('small');
    id.className = 'column-id';
    id.textContent = column.column_id;
    nameCell.append(name, id);
    const typeCell = document.createElement('td');
    typeCell.textContent = column.data_types.join(' / ');
    typeCell.title = column.definitions.map(item => `${item.report_template_no}：${item.field_name} · ${item.data_type}${item.unit ? `（${item.unit}）` : ''}`).join('\n');
    typeCell.classList.toggle('type-conflict', column.data_type === null);
    const taskCell = document.createElement('td');
    taskCell.textContent = column.task_name;
    const keyCell = document.createElement('td');
    const key = document.createElement('span');
    key.textContent = column.is_join_key ? '关联键' : '—';
    if (column.is_join_key) key.className = 'key-badge';
    keyCell.append(key);
    row.append(nameCell, typeCell, taskCell, keyCell);
    $('schema-rows').append(row);
  }
  const conflicts = data.columns.filter(column => column.data_type === null).length;
  $('schema-warning').hidden = conflicts === 0;
  $('schema-warning').textContent = `${conflicts} 列在历史模板中的类型不一致，已保留各模板的字段定义。`;
  $('schema-summary').textContent = `${data.columns.length} 列 · ${data.columns.filter(column => column.is_join_key).length} 个关联键 · 仅表头，不含填报值`;
  $('schema-empty').hidden = data.columns.length > 0;
  $('schema-table-wrap').hidden = data.columns.length === 0;
  $('schema-json').textContent = JSON.stringify(data, null, 2);
  $('schema-count').textContent = `${data.columns.length} 列`;
  $('schema-count').hidden = false;
  $('schema-tab').disabled = false;
  $('analysis-tab').disabled = data.columns.length === 0;
  showSchema(true);
}

$('relations-tab').onclick = () => showSchema(false);
$('schema-tab').onclick = () => showSchema(true);
$('analysis-tab').onclick = () => showView('analysis');
for (const id of ['relations-tab', 'schema-tab', 'analysis-tab']) {
  $(id).onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const tabs = ['relations-tab', 'schema-tab', 'analysis-tab'].filter(tab => !$(tab).disabled);
    const index = tabs.indexOf(id);
    const target = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs.at(-1) : tabs[(index + (event.key === 'ArrowLeft' ? -1 : 1) + tabs.length) % tabs.length];
    $(target).click(); $(target).focus();
  };
}

function invalidateAnalysis() {
  analysisRevision++;
  $('analysis-result').hidden = true;
  $('analysis-error').hidden = true;
  $('analysis-status').textContent = '';
  for (const id of ['analysis-sql', 'analysis-prepared-sql', 'analysis-sources']) $(id).textContent = '';
}

async function runAnalysis() {
  const prompt = $('analysis-prompt').value.trim();
  invalidateAnalysis();
  if (!prompt) {
    $('analysis-error').textContent = '请输入分析需求。';
    $('analysis-error').hidden = false;
    $('analysis-prompt').focus();
    return;
  }
  const version = analysisRevision;
  const schemaVersion = schemaRevision;
  const snapshot = JSON.stringify(relationDraft());
  generatingAnalysis = true;
  $('analysis-status').textContent = '正在调用本地模型，首次加载可能较慢…';
  syncRelationState();
  try {
    const data = await postJSON('/api/analysis', {...JSON.parse(snapshot), prompt});
    if (version !== analysisRevision || schemaVersion !== schemaRevision || prompt !== $('analysis-prompt').value.trim()) return;
    $('analysis-model').textContent = data.model;
    $('analysis-title').textContent = data.status === 'clarify' ? '需要补充信息' : data.chart.title;
    $('analysis-message').textContent = data.message;
    $('analysis-sql-result').hidden = data.status !== 'ok';
    if (data.status === 'ok') {
      $('analysis-sql').textContent = data.sql;
      $('analysis-chart-summary').textContent = `${{bar: '柱状图', line: '折线图', pie: '饼图'}[data.chart.type]} · ${data.chart.series.map(series => series.label).join('、')}`;
      $('analysis-prepared-sql').textContent = data.prepared_sql;
      $('analysis-sources').textContent = data.sources.map(table => `${table.alias} = ${table.task_name}\n${table.fields.map(field => `  ${field.text_column}${field.number_column ? ' / ' + field.number_column : ''} = ${field.name} [${field.column_id}]`).join('\n')}`).join('\n\n');
    }
    $('analysis-result').hidden = false;
  } catch (error) {
    if (version !== analysisRevision || schemaVersion !== schemaRevision) return;
    $('analysis-error').textContent = error instanceof TypeError ? '连接失败，请重试。' : error.message;
    $('analysis-error').hidden = false;
  } finally {
    generatingAnalysis = false;
    $('analysis-status').textContent = '';
    syncRelationState();
  }
}

$('analysis-prompt').oninput = invalidateAnalysis;
$('confirm-relations').onclick = () => {
  if ($('confirm-relations').disabled) return;
  if (!$('analysis-view').hidden) runAnalysis();
  else generateSchema();
};

$('search-form').onsubmit = event => { event.preventDefault(); loadTasks(); };
$('search').addEventListener('search', () => { if (!$('search').value) loadTasks(); });
$('reset-search').onclick = () => { $('search').value = ''; setTaskFilter(false); loadTasks(); };
$('retry').onclick = loadTasks;
$('retry-fields').onclick = scheduleFields;
$('all-filter').onclick = () => setTaskFilter(false);
$('selected-filter').onclick = () => setTaskFilter(true);
$('select-all').onchange = () => {
  const checked = $('select-all').checked;
  for (const task of currentTasks()) {
    if (checked) selected.set(task.report_task_no, task);
    else selected.delete(task.report_task_no);
  }
  scheduleFields();
  render();
};
$('clear').onclick = () => { selected.clear(); scheduleFields(); render(); };
loadTasks();
