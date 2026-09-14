import unittest
from evalnoise.investigation import provenance, timeline, selected, matrix


class InvestigationTests(unittest.TestCase):
    def test_unknown_is_not_equal_and_changed_contract_incompatible(self):
        self.assertEqual(provenance({}, {})['rows'][0]['state'],'unknown')
        self.assertEqual(provenance({'task_contracts':{'a':'1'}},{'task_contracts':{'a':'2'}})['verdict'],'incompatible')
        self.assertEqual(provenance({'engine_identity_final':{'stable':False}}, {})['verdict'],'incompatible')

    def test_export_selection_and_full_matrix(self):
        rows=[('a',{'id':'a','task':'cpu','profile':'one','status':'passed'}),('b',{'id':'b','task':'ram','profile':'two','status':'oom_killed'})]
        self.assertEqual(selected(rows,{'status':'oom_killed','search':'ram'}),rows[1:])
        self.assertEqual(len(matrix(rows)),2)

    def test_plots_keep_missing_missing_and_escape_no_data(self):
        self.assertIn('No plottable',timeline({}))
        sample={'received_elapsed_s':1,'raw':{'memory_stats':{'usage':1048576},'cpu_stats':{'cpu_usage':{'total_usage':1000000}}}}
        text=timeline({'telemetry':[sample,{'raw':{}}]})
        self.assertIn('Memory usage (MiB)',text)
        self.assertIn('1 unavailable',text)
        self.assertNotIn('Cumulative throttled time',text)
        self.assertNotIn('<polyline',text)
