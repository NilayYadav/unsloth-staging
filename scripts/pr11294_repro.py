"""Real public GSM8K route/loader assertions; identical on both implementation refs."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--repo', type=Path, default=Path.cwd())
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
sys.path.insert(0, str(a.repo.resolve() / 'studio/backend'))
spec = importlib.util.spec_from_file_location('seed_under_test', a.repo / 'studio/backend/routes/data_recipe/seed.py')
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

from huggingface_hub import HfApi, HfFileSystem
from data_designer.config.seed_source import HuggingFaceSeedSource
from data_designer.engine.resources.seed_reader import HuggingFaceSeedReader
from data_designer.engine.secret_resolver import PlaintextResolver

repo = 'openai/gsm8k'
revision = HfApi(token=False).dataset_info(repo).sha
fs = HfFileSystem(token=False)
facts = {'dataset': repo, 'dataset_revision': revision, 'cases': []}
for subset in ('main', 'socratic'):
    result = seed.inspect_seed_dataset(seed.SeedInspectRequest(
        dataset_name=repo, subset=subset, split='train', preview_size=2,
    ), allow_ambient_token=False)
    files = fs.glob(result.resolved_path)
    reader = HuggingFaceSeedReader()
    reader.attach(HuggingFaceSeedSource(path=result.resolved_path), PlaintextResolver())
    row_count = reader.get_seed_dataset_size()
    batch = reader.create_batch_reader(batch_size=2, index_range=None, shuffle=False)
    rows = batch.read_next_batch().to_pandas()
    case = {
        'subset': subset, 'split': 'train', 'resolved_path': result.resolved_path,
        'matched_files': files, 'loaded_rows': row_count,
        'preview_first_question': result.preview_rows[0]['question'],
        'preview_first_answer': result.preview_rows[0]['answer'],
        'loaded_first_question': rows.iloc[0]['question'],
        'loaded_first_answer': rows.iloc[0]['answer'],
        'preview_matches_run': result.preview_rows[0]['question'] == rows.iloc[0]['question'],
        'subset_correct': all('/' + subset + '/' in f for f in files),
        'train_only': all('/train-' in f for f in files),
    }
    facts['cases'].append(case)
    print(json.dumps(case), flush=True)
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(facts, indent=2) + '\n')
assert all(c['train_only'] and c['subset_correct'] and c['loaded_rows'] == 7473
           and c['preview_matches_run'] for c in facts['cases']), 'CHOSEN_SPLIT_SUBSET_ONLY: recipe must load exactly 7473 chosen-subset training rows and match its preview'
print('PASS CHOSEN_SPLIT_SUBSET_ONLY: main and socratic each load 7473 training rows; preview matches run')
