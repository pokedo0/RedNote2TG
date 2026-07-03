import random
import unittest

def generate_one_combination():
    # 必带, 2个：基础词：[高跟，凉鞋，凉拖],  [蕾丝，水晶，透明, 裸色, 水钻]
    must_have_1 = ["高跟", "凉鞋", "凉拖"]
    must_have_2 = ["蕾丝", "水晶", "透明", "裸色", "水钻"]
    
    words = []
    # 1. 必带组固定选择 2 个词
    words.append(random.choice(must_have_1))
    words.append(random.choice(must_have_2))
    
    # 2. 长尾词（必须抽 1-2 个）
    long_tail_groups = [
        ["穿搭", "搭配", "ootd"],
        ["裙"],
        ["夏日"],
        ["仙女", "女神", "姐", "人妻", "御姐"]
    ]
    k_long = random.choice([1, 2])
    chosen_long_groups = random.sample(long_tail_groups, k_long)
    for g in chosen_long_groups:
        words.append(random.choice(g))
        
    # 3. 属性（50%概率触发，抽 1-2 个）
    if random.random() < 0.5:
        attr_groups = [
            ["紫色", "白色", "黑色", "银色"],
            ["粗跟", "一字带"],
            ["露脚趾"],
            ["足控", "腿"]
        ]
        k_attr = random.choice([1, 2])
        chosen_attr_groups = random.sample(attr_groups, k_attr)
        for g in chosen_attr_groups:
            words.append(random.choice(g))
            
    # 4. 品牌（10%概率触发，抽 1 个）
    if random.random() < 0.1:
        brand = random.choice(["zara", "小ck", "百丽", "思加图"])
        words.append(brand)
        
    return words

def generate_combinations(n=20):
    combinations = []
    attempts = 0
    max_attempts = 10000
    while len(combinations) < n and attempts < max_attempts:
        attempts += 1
        words = generate_one_combination()
        if 2 <= len(words) <= 6:
            combinations.append(words)
    return combinations

class TestKeywordGenerator(unittest.TestCase):
    def test_generate_combinations(self):
        results = generate_combinations(20)
        print("\n--- Generated 20 Combinations (By Probabilities) ---")
        for i, comb in enumerate(results, 1):
            print(f"{i:2d}. {', '.join(comb)} (Count: {len(comb)})")
        
        self.assertEqual(len(results), 20)
        for comb in results:
            self.assertTrue(2 <= len(comb) <= 6)
            # Ensure must-have constraints
            has_must_1 = any(w in ["高跟", "凉鞋", "凉拖"] for w in comb)
            has_must_2 = any(w in ["蕾丝", "水晶", "透明", "裸色", "水钻"] for w in comb)
            self.assertTrue(has_must_1)
            self.assertTrue(has_must_2)

if __name__ == "__main__":
    unittest.main()
