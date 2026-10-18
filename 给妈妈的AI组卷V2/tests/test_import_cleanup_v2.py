from copy import deepcopy
import unittest
from question_import_v2.cleanup import clean_question
from question_import_v2.splitter import QUESTION

def paragraph(text,id='p'):
    return {'id':id,'type':'paragraph','align':'left','inlines':[{'id':id+'-text','type':'text','text':text,'style':{'bold':True}}]}
def question(text):
    return {'id':'q','number':'63','source':{'number':'63'},'body':[paragraph(text)],'options':[],'subquestions':[],'answers':[],'explanations':[],'issues':[]}

class CleanupTests(unittest.TestCase):
    def test_number_score_variants_preserve_source(self):
        for text in ['63. （6分）根据图回答','63 根据图回答','63根据图回答','63（6分）根据图回答','６３．（6分）根据图回答','63）根据图回答','第63题：（本题6分）根据图回答','（每空1分，共6分）根据图回答']:
            q=question(text);source=deepcopy(q['source']);clean_question(q)
            self.assertEqual(q['body'][0]['inlines'][0]['text'],'根据图回答',text)
            self.assertEqual(q['source'],source);self.assertEqual(q['number'],'63')
            self.assertTrue(q['body'][0]['inlines'][0]['style']['bold'])
            self.assertFalse(clean_question(q)['prefixes_removed'])
    def test_measurements_and_conditions_stay(self):
        for text in ['63.4g样品','63 g样品','63种物质','63 个原子','（20℃）反应','（2分子水）反应','2026年某实验']:
            q=question(text);before=deepcopy(q);clean_question(q);self.assertEqual(q,before,text)
    def test_bare_numbers_split_without_recognizing_quantities(self):
        for text in ['63 根据下图回答','63根据图回答','63 （6分）回答问题']:
            self.assertEqual(QUESTION.match(text)[1],'63')
        for text in ['25.8g水','3种气体','3 种气体','2026年某实验','2 mol水']:
            self.assertIsNone(QUESTION.match(text),text)
    def test_score_across_styled_runs(self):
        q=question('（');q['body'][0]['inlines'] += [{'id':'n2','type':'text','text':' 6 分','style':{'italic':True}},{'id':'n3','type':'text','text':'）真正题干'}]
        clean_question(q);self.assertEqual(q['body'][0]['inlines'],[{'id':'n3','type':'text','text':'真正题干'}])
    def test_score_embedded_in_native_equation_object(self):
        q=question('庄子');q['body'][0]['inlines'].insert(0,{'id':'formula','type':'formula','latex':r'（ \mathrm{9} \mathrm{分} ） （ \mathrm{1} ） 《','source_ref':'ref','status':'restored'})
        clean_question(q);self.assertEqual(q['body'][0]['inlines'][0],{'id':'formula','type':'text','text':'(1)《'})
    def test_identity_options_removed_only_with_prior_representation(self):
        q=question('根据图回答');q['body'].append({'id':'img','type':'image','asset_id':'a','width_mm':1,'height_mm':1})
        q['options']=[{'id':letter,'label':letter,'blocks':[paragraph(letter,letter+'p')]} for letter in 'ABCD']
        q['answers']=[{'id':'ans','target_id':'B','blocks':[paragraph('B','ansp')]}]
        clean_question(q);self.assertEqual(q['options'],[]);self.assertEqual(q['answers'][0]['target_id'],'q')
    def test_meaningful_and_letter_only_choices_without_diagram_survive(self):
        for choices in [('甲','乙','丙','丁'),('A','B','C','D')]:
            q=question('请选一种元素');q['options']=[{'id':letter,'label':letter,'blocks':[paragraph(text,letter+'p')]} for letter,text in zip('ABCD',choices)]
            before=deepcopy(q['options']);clean_question(q);self.assertEqual(q['options'],before)
    def test_redundant_choices_under_already_labeled_table_content(self):
        q=question('请选择');q['body'] += [paragraph(letter+'．选项正文',letter+'stem') for letter in 'ABCD']
        q['options']=[{'id':letter,'label':letter,'blocks':[paragraph(letter,letter+'option')]} for letter in 'ABCD']
        clean_question(q);self.assertEqual(q['options'],[])

if __name__=='__main__':unittest.main()
