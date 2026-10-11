from typing import List
from models.LinUCB import build_state_for_query


def retrieval_service(queries: List[str], file_names: List[str], topk: int, embeder, linucb, pca_assets, multi_img_retriever):
    search_results_list = []
    for i in range(len(queries)):
        query = queries[i]
        feature, query_np = build_state_for_query(query, embeder, pca_assets)
        selected_topn, ucb_scores = linucb.select_arm(feature)
        score_results = multi_img_retriever.multi_img_search(query_np, file_names, topk, selected_topn)
        search_results = [sitem[3] for sitem in score_results]
        search_results_list.append(search_results)
    return search_results_list


def get_overlap_rate(search_results, history_pages):
    overlap = sum(1 for s in search_results if s in history_pages)
    return overlap / len(search_results)


def deduplication_split(search_results_list, topk):
    search_results = []
    for s in search_results_list:
        search_results = list(set(search_results) | set(s))
    return [search_results[i:i + topk] for i in range(0, len(search_results), topk)]
