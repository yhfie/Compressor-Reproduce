from __future__ import absolute_import, division, print_function

import os
import json
import torch
import random
import logging
import argparse
import numpy as np
import multiprocessing
import torch.nn.functional as F
import csv

from tqdm import tqdm
from tokenizers import Tokenizer
from model import Model, distill_loss
from tree_sitter import Language, Parser
from torch.utils.data import DataLoader, Dataset, SequentialSampler, RandomSampler

from parser import DFG_python,DFG_java,DFG_ruby,DFG_go,DFG_php,DFG_javascript
from parser import (remove_comments_and_docstrings,
                    tree_to_token_index, index_to_code_token)
from transformers import (AdamW, get_linear_schedule_with_warmup,
                          RobertaConfig, RobertaForSequenceClassification)

cpu_cont = 16
logger = logging.getLogger(__name__)

dfg_function={
    "python":DFG_python,
    "java":DFG_java,
    "ruby":DFG_ruby,
    "go":DFG_go,
    "php":DFG_php,
    "javascript":DFG_javascript
}

#load parsers
parsers={}        
for lang in dfg_function:
    LANGUAGE = Language("parser/my-languages.so", lang)
    parser = Parser()
    parser.set_language(LANGUAGE) 
    parser = [parser,dfg_function[lang]]    
    parsers[lang]= parser
    
    
#remove comments, tokenize code and extract dataflow                                        
def extract_dataflow(code, parser,lang):
    #remove comments
    try:
        code=remove_comments_and_docstrings(code,lang)
    except:
        pass    
    #obtain dataflow
    if lang=="php":
        code="<?php"+code+"?>"    
    try:
        tree = parser[0].parse(bytes(code,"utf8"))    
        root_node = tree.root_node  
        tokens_index=tree_to_token_index(root_node)     
        code=code.split("\n")
        code_tokens=[index_to_code_token(x,code) for x in tokens_index]  
        index_to_code={}
        for idx,(index,code) in enumerate(zip(tokens_index,code_tokens)):
            index_to_code[index]=(idx,code)  
        try:
            DFG,_=parser[1](root_node,index_to_code,{}) 
        except:
            DFG=[]
        DFG=sorted(DFG,key=lambda x:x[1])
        indexs=set()
        for d in DFG:
            if len(d[-1])!=0:
                indexs.add(d[1])
            for x in d[-1]:
                indexs.add(x)
        new_DFG=[]
        for d in DFG:
            if d[1] in indexs:
                new_DFG.append(d)
        dfg=new_DFG
    except:
        dfg=[]
    return code_tokens,dfg


class InputFeatures(object):
    """A single training/test features for a example."""
    def __init__(self,
             input_tokens_1,
             input_ids_1,
             position_idx_1,
             dfg_to_code_1,
             dfg_to_dfg_1,
             input_tokens_2,
             input_ids_2,
             position_idx_2,
             dfg_to_code_2,
             dfg_to_dfg_2,
             label,
             url1,
             url2,
             soft_label = [0.1, 0.1]

    ):
        #The first code function
        self.input_tokens_1 = input_tokens_1
        self.input_ids_1 = input_ids_1
        self.position_idx_1=position_idx_1
        self.dfg_to_code_1=dfg_to_code_1
        self.dfg_to_dfg_1=dfg_to_dfg_1
        
        #The second code function
        self.input_tokens_2 = input_tokens_2
        self.input_ids_2 = input_ids_2
        self.position_idx_2=position_idx_2
        self.dfg_to_code_2=dfg_to_code_2
        self.dfg_to_dfg_2=dfg_to_dfg_2
        
        #label
        self.label=label
        self.url1=url1
        self.url2=url2
        self.soft_label = soft_label
        

def convert_examples_to_features(item):
    #source
    url1,url2,label,tokenizer, args,cache,url_to_code, s=item
    parser=parsers["java"]
    
    for url in [url1,url2]:
        if url not in cache:
            func=url_to_code[url]
            
            #extract data flow
            code_tokens,dfg=extract_dataflow(func,parser,"java")
            code_tokens=[tokenizer.encode(x).tokens for idx,x in enumerate(code_tokens)]
            ori2cur_pos={}
            ori2cur_pos[-1]=(0,0)
            for i in range(len(code_tokens)):
                ori2cur_pos[i]=(ori2cur_pos[i-1][1],ori2cur_pos[i-1][1]+len(code_tokens[i]))    
            code_tokens=[y for x in code_tokens for y in x]  
            
            #truncating
            code_tokens=code_tokens[:args.code_length+args.data_flow_length-3-min(len(dfg),args.data_flow_length)][:512-3]
            source_tokens =["<s>"]+code_tokens+["</s>"]
            source_ids =  [tokenizer.token_to_id(tok) for tok in source_tokens]
            position_idx = [i+tokenizer.token_to_id("<pad>") + 1 for i in range(len(source_tokens))]
            dfg=dfg[:args.code_length+args.data_flow_length-len(source_tokens)]
            source_tokens+=[x[0] for x in dfg]
            position_idx+=[0 for x in dfg]
            source_ids+=[tokenizer.token_to_id("<unk>") for x in dfg]
            padding_length=args.code_length+args.data_flow_length-len(source_ids)
            position_idx+=[tokenizer.token_to_id("<pad>")]*padding_length
            source_ids+=[tokenizer.token_to_id("<pad>")]*padding_length      
            
            #reindex
            reverse_index={}
            for idx,x in enumerate(dfg):
                reverse_index[x[1]]=idx
            for idx,x in enumerate(dfg):
                dfg[idx]=x[:-1]+([reverse_index[i] for i in x[-1] if i in reverse_index],)    
            dfg_to_dfg=[x[-1] for x in dfg]
            dfg_to_code=[ori2cur_pos[x[1]] for x in dfg]
            length=len(["<s>"])
            dfg_to_code=[(x[0]+length,x[1]+length) for x in dfg_to_code]        
            cache[url]=source_tokens,source_ids,position_idx,dfg_to_code,dfg_to_dfg

        
    source_tokens_1,source_ids_1,position_idx_1,dfg_to_code_1,dfg_to_dfg_1=cache[url1]   
    source_tokens_2,source_ids_2,position_idx_2,dfg_to_code_2,dfg_to_dfg_2=cache[url2]   
    return InputFeatures(source_tokens_1,source_ids_1,position_idx_1,dfg_to_code_1,dfg_to_dfg_1,
                   source_tokens_2,source_ids_2,position_idx_2,dfg_to_code_2,dfg_to_dfg_2,
                     label,url1,url2, s)


class TextDataset(Dataset):
    def __init__(self, tokenizer, args, file_path="train"):
        postfix=file_path.split("/")[-1].split(".txt")[0]

        self.examples = []
        self.args=args
        index_filename=file_path
        
        logger.info("Creating features from index file at %s ", index_filename)
        url_to_code={}

        folder = "/".join(file_path.split("/")[:-1])
        
        tokenizer_path = os.path.join(folder, "BPE" + "_" + str(args.vocab_size) + ".json")
        tokenizer = Tokenizer.from_file(tokenizer_path)
        logger.info("Creating features from dataset file at %s", file_path)
        with open("/".join(index_filename.split("/")[:-1])+"/data.jsonl") as f:
            for line in f:
                line=line.strip()
                js=json.loads(line)
                url_to_code[js["idx"]]=js["func"]
                
        #load code function according to index
        data=[]
        cache={}
        f=open(index_filename)
        with open(index_filename) as f:
            for line in f:
                line=line.strip()
                if "train" in postfix:
                    url1, url2, label, pred = line.split("\t")
                else:
                    url1, url2, label = line.split("\t")

                if url1 not in url_to_code or url2 not in url_to_code:
                    continue
                if label=="0":
                    label=0
                else:
                    label=1
                data.append((url1,url2,label,tokenizer, args,cache,url_to_code))
        if "train" in postfix:
            soft_labels = np.load(os.path.join(folder, "preds_unlabel_train_gcb.npy")).tolist()
        
        _mp_data = []
        for i, d in enumerate(data):
            lst = list(d)
            # lst.append(tokenizer)
            if "train" in postfix:
                lst.append(soft_labels[i])
            else:
                lst.append([0.1, 0.1])
            _mp_data.append(tuple(lst))

        pool = multiprocessing.Pool(multiprocessing.cpu_count())
        self.examples = pool.map(convert_examples_to_features, tqdm(_mp_data, total=len(_mp_data)))    

        if "train" in file_path:
            for idx, example in enumerate(self.examples[:3]):
                logger.info("*** Example ***")
                logger.info("idx: {}".format(idx))
                logger.info("label: {}".format(example.label))
                logger.info("soft label: {}".format(example.soft_label))
                logger.info("input_tokens_1: {}".format([x.replace("\u0120","_") for x in example.input_tokens_1]))
                logger.info("input_ids_1: {}".format(" ".join(map(str, example.input_ids_1))))       
                logger.info("position_idx_1: {}".format(example.position_idx_1))
                logger.info("dfg_to_code_1: {}".format(" ".join(map(str, example.dfg_to_code_1))))
                logger.info("dfg_to_dfg_1: {}".format(" ".join(map(str, example.dfg_to_dfg_1))))
                
                logger.info("input_tokens_2: {}".format([x.replace("\u0120","_") for x in example.input_tokens_2]))
                logger.info("input_ids_2: {}".format(" ".join(map(str, example.input_ids_2))))       
                logger.info("position_idx_2: {}".format(example.position_idx_2))
                logger.info("dfg_to_code_2: {}".format(" ".join(map(str, example.dfg_to_code_2))))
                logger.info("dfg_to_dfg_2: {}".format(" ".join(map(str, example.dfg_to_dfg_2))))


    def __len__(self):
        return len(self.examples)
    
    def __getitem__(self, item):
        #calculate graph-guided masked function
        attn_mask_1= np.zeros((self.args.code_length+self.args.data_flow_length,
                        self.args.code_length+self.args.data_flow_length),dtype=bool)
        #calculate begin index of node and max length of input
        node_index=sum([i>1 for i in self.examples[item].position_idx_1])
        max_length=sum([i!=1 for i in self.examples[item].position_idx_1])
        #sequence can attend to sequence
        attn_mask_1[:node_index,:node_index]=True
        #special tokens attend to all tokens
        for idx,i in enumerate(self.examples[item].input_ids_1):
            if i in [0,2]:
                attn_mask_1[idx,:max_length]=True
        #nodes attend to code tokens that are identified from
        for idx,(a,b) in enumerate(self.examples[item].dfg_to_code_1):
            if a<node_index and b<node_index:
                attn_mask_1[idx+node_index,a:b]=True
                attn_mask_1[a:b,idx+node_index]=True
        #nodes attend to adjacent nodes 
        for idx,nodes in enumerate(self.examples[item].dfg_to_dfg_1):
            for a in nodes:
                if a+node_index<len(self.examples[item].position_idx_1):
                    attn_mask_1[idx+node_index,a+node_index]=True  
                    
        #calculate graph-guided masked function
        attn_mask_2= np.zeros((self.args.code_length+self.args.data_flow_length,
                        self.args.code_length+self.args.data_flow_length),dtype=bool)
        #calculate begin index of node and max length of input
        node_index=sum([i>1 for i in self.examples[item].position_idx_2])
        max_length=sum([i!=1 for i in self.examples[item].position_idx_2])
        #sequence can attend to sequence
        attn_mask_2[:node_index,:node_index]=True
        #special tokens attend to all tokens
        for idx,i in enumerate(self.examples[item].input_ids_2):
            if i in [0,2]:
                attn_mask_2[idx,:max_length]=True
        #nodes attend to code tokens that are identified from
        for idx,(a,b) in enumerate(self.examples[item].dfg_to_code_2):
            if a<node_index and b<node_index:
                attn_mask_2[idx+node_index,a:b]=True
                attn_mask_2[a:b,idx+node_index]=True
        #nodes attend to adjacent nodes 
        for idx,nodes in enumerate(self.examples[item].dfg_to_dfg_2):
            for a in nodes:
                if a+node_index<len(self.examples[item].position_idx_2):
                    attn_mask_2[idx+node_index,a+node_index]=True                      
                    
        return (torch.tensor(self.examples[item].input_ids_1),
                torch.tensor(self.examples[item].position_idx_1),
                torch.tensor(attn_mask_1), 
                torch.tensor(self.examples[item].input_ids_2),
                torch.tensor(self.examples[item].position_idx_2),
                torch.tensor(attn_mask_2),                 
                torch.tensor(self.examples[item].label),
                torch.tensor(self.examples[item].soft_label))


def set_seed(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)


def train(args, train_dataset, model, tokenizer):
    """ Train the model """
    
    #build dataloader
    train_sampler = RandomSampler(train_dataset)
    train_dataloader = DataLoader(train_dataset, sampler=train_sampler, batch_size=args.train_batch_size,num_workers=4)
    
    args.max_steps=args.epochs*len( train_dataloader)
    args.save_steps=len(train_dataloader)
    args.warmup_steps=args.max_steps//5
    model.to(args.device)
    
    # Prepare optimizer and schedule (linear warmup and decay)
    no_decay = ["bias", "LayerNorm.weight"]
    optimizer_grouped_parameters = [
        {"params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         "weight_decay": args.weight_decay},
        {"params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], "weight_decay": 0.0}
    ]
    optimizer = AdamW(optimizer_grouped_parameters, lr=args.learning_rate, eps=args.adam_epsilon)
    scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=args.warmup_steps,
                                                num_training_steps=args.max_steps)

    # multi-gpu training
    if args.n_gpu > 1:
        model = torch.nn.DataParallel(model)

    # Train!
    logger.info("***** Running training *****")
    logger.info("  Num examples = %d", len(train_dataset))
    logger.info("  Num Epochs = %d", args.epochs)
    logger.info("  Instantaneous batch size per GPU = %d", args.train_batch_size//max(args.n_gpu, 1))
    logger.info("  Total train batch size = %d",args.train_batch_size*args.gradient_accumulation_steps)
    logger.info("  Gradient Accumulation steps = %d", args.gradient_accumulation_steps)
    logger.info("  Total optimization steps = %d", args.max_steps)
    
    global_step=0
    tr_loss, logging_loss,avg_loss,tr_nb,tr_num,train_loss = 0.0, 0.0,0.0,0,0,0
    best_f1=0

    model.zero_grad()
 
    for idx in range(args.epochs): 
        bar = tqdm(train_dataloader,total=len(train_dataloader))
        tr_num=0
        train_loss=0
        for step, batch in enumerate(bar):
            (inputs_ids_1,position_idx_1,attn_mask_1,
            inputs_ids_2,position_idx_2,attn_mask_2,
            labels, soft_knowledge)=[x.to(args.device)  for x in batch]
            model.train()
            _,logits = model(inputs_ids_1,position_idx_1,attn_mask_1,inputs_ids_2,position_idx_2,attn_mask_2,labels)
            loss = distill_loss(logits, soft_knowledge)
            if args.n_gpu > 1:
                loss = loss.mean()
                
            if args.gradient_accumulation_steps > 1:
                loss = loss / args.gradient_accumulation_steps

            loss.backward()

            tr_loss += loss.item()
            tr_num+=1
            train_loss+=loss.item()
            if avg_loss==0:
                avg_loss=tr_loss
                
            avg_loss=round(train_loss/tr_num,5)
            bar.set_description("epoch {} loss {}".format(idx,avg_loss))
              
            if (step + 1) % args.gradient_accumulation_steps == 0:
                optimizer.step()
                optimizer.zero_grad()
                scheduler.step()  
                global_step += 1
                output_flag=True
                avg_loss=round(np.exp((tr_loss - logging_loss) /(global_step- tr_nb)),4)

                if global_step % args.save_steps == 0:
                    results = evaluate(args, model, tokenizer, eval_when_training=True)    
                    
                    # Save model checkpoint
                    if results["eval_acc"]>best_f1:
                        best_f1=results["eval_acc"]
                        logger.info("  "+"*"*20)  
                        logger.info("  Best acc:%s",round(best_f1,4))
                        logger.info("  "+"*"*20)                          
                        
                        checkpoint_prefix = ""
                        output_dir = os.path.join(args.output_dir, "{}".format(checkpoint_prefix), args.size)                        
                        if not os.path.exists(output_dir):
                            os.makedirs(output_dir)                        
                        model_to_save = model.module if hasattr(model,"module") else model
                        output_dir = os.path.join(output_dir, "{}".format("model.bin")) 
                        torch.save(model_to_save.state_dict(), output_dir)
                        logger.info("Saving model checkpoint to %s", output_dir)
                        
def evaluate(args, model, tokenizer, eval_when_training=False):
    #build dataloader
    eval_dataset = TextDataset(tokenizer, args, file_path=args.eval_data_file)
    eval_sampler = SequentialSampler(eval_dataset)
    eval_dataloader = DataLoader(eval_dataset, sampler=eval_sampler,batch_size=args.eval_batch_size,num_workers=4)

    # multi-gpu evaluate
    if args.n_gpu > 1 and eval_when_training is False:
        model = torch.nn.DataParallel(model)

    # Eval!
    logger.info("***** Running evaluation *****")
    logger.info("  Num examples = %d", len(eval_dataset))
    logger.info("  Batch size = %d", args.eval_batch_size)
    
    eval_loss = 0.0
    nb_eval_steps = 0
    model.eval()
    logits=[]  
    y_trues=[]
    for batch in tqdm(eval_dataloader):
        (inputs_ids_1,position_idx_1,attn_mask_1,
        inputs_ids_2,position_idx_2,attn_mask_2,
        labels, soft_label)=[x.to(args.device)  for x in batch]
        with torch.no_grad():
            lm_loss,logit = model(inputs_ids_1,position_idx_1,attn_mask_1,inputs_ids_2,position_idx_2,attn_mask_2,labels)
            eval_loss += lm_loss.mean().item()
            logit = F.softmax(logit)
            logits.append(logit.cpu().numpy())
            y_trues.append(labels.cpu().numpy())
        nb_eval_steps += 1

    #calculate scores
    logits=np.concatenate(logits,0)
    y_trues=np.concatenate(y_trues,0)
    best_threshold=0.5

    y_preds=logits[:,1]>best_threshold
    from sklearn.metrics import recall_score
    recall=recall_score(y_trues, y_preds, average="macro")
    from sklearn.metrics import precision_score
    precision=precision_score(y_trues, y_preds, average="macro")   
    from sklearn.metrics import f1_score
    f1=f1_score(y_trues, y_preds, average="macro")             
    result = {
        "eval_acc": np.mean(y_trues==y_preds),
        "eval_recall": float(recall),
        "eval_precision": float(precision),
        "eval_f1": float(f1),
        "eval_threshold":best_threshold,
        
    }

    logger.info("***** Eval results *****")
    for key in sorted(result.keys()):
        logger.info("  %s = %s", key, str(round(result[key],4)))

    return result

def test(args, model, tokenizer, best_threshold=0):
    #build dataloader
    eval_dataset = TextDataset(tokenizer, args, file_path=args.test_data_file)
    eval_sampler = SequentialSampler(eval_dataset)
    eval_dataloader = DataLoader(eval_dataset, sampler=eval_sampler, batch_size=args.eval_batch_size,num_workers=4)

    # multi-gpu evaluate
    if args.n_gpu > 1:
        model = torch.nn.DataParallel(model)

    # Eval!
    logger.info("***** Running Test *****")
    logger.info("  Num examples = %d", len(eval_dataset))
    logger.info("  Batch size = %d", args.eval_batch_size)
    eval_loss = 0.0
    nb_eval_steps = 0
    model.eval()
    logits=[]  
    y_trues=[]
    for batch in tqdm(eval_dataloader):
        (inputs_ids_1,position_idx_1,attn_mask_1,
        inputs_ids_2,position_idx_2,attn_mask_2,
        labels, soft_label)=[x.to(args.device)  for x in batch]
        with torch.no_grad():
            lm_loss,logit = model(inputs_ids_1,position_idx_1,attn_mask_1,inputs_ids_2,position_idx_2,attn_mask_2,labels)
            logit = F.softmax(logit)
            eval_loss += lm_loss.mean().item()
            logits.append(logit.cpu().numpy())
            y_trues.append(labels.cpu().numpy())
        nb_eval_steps += 1
    best_threshold=0.5

    #output result
    logits=np.concatenate(logits,0)

    y_preds=logits[:,1]>best_threshold
    y_trues=np.concatenate(y_trues,0)
    from sklearn.metrics import recall_score
    recall=recall_score(y_trues, y_preds, average="macro")
    from sklearn.metrics import precision_score
    precision=precision_score(y_trues, y_preds, average="macro")   
    from sklearn.metrics import f1_score
    f1=f1_score(y_trues, y_preds, average="macro")             
    result = {
        "test_acc": np.mean(y_trues==y_preds),
        "test_recall": float(recall),
        "test_precision": float(precision),
        "test_f1": float(f1)
    }

    logger.info("***** Test results *****")
    for key in sorted(result.keys()):
        logger.info("  %s = %s", key, str(round(result[key],4)))

    return result

                                           
def main():
    parser = argparse.ArgumentParser()

    ## Required parameters
    parser.add_argument("--train_data_file", default=None, type=str, required=True,
                        help="The input training data file (a text file).")
    parser.add_argument("--output_dir", default=None, type=str, required=True,
                        help="The output directory where the model predictions and checkpoints will be written.")

    ## Other parameters
    parser.add_argument("--eval_data_file", default=None, type=str,
                        help="An optional input evaluation data file to evaluate the perplexity on (a text file).")
    parser.add_argument("--test_data_file", default=None, type=str,
                        help="An optional input evaluation data file to evaluate the perplexity on (a text file).")
                    
    parser.add_argument("--model_name_or_path", default=None, type=str,
                        help="The model checkpoint for weights initialization.")

    parser.add_argument("--config_name", default="", type=str,
                        help="Optional pretrained config name or path if not the same as model_name_or_path")
    parser.add_argument("--tokenizer_name", default="", type=str,
                        help="Optional pretrained tokenizer name or path if not the same as model_name_or_path")

    parser.add_argument("--code_length", default=256, type=int,
                        help="Optional Code input sequence length after tokenization.") 
    parser.add_argument("--data_flow_length", default=64, type=int,
                        help="Optional Data Flow input sequence length after tokenization.") 
    parser.add_argument("--do_train", action="store_true",
                        help="Whether to run training.")
    parser.add_argument("--do_eval", action="store_true",
                        help="Whether to run eval on the dev set.")
    parser.add_argument("--do_test", action="store_true",
                        help="Whether to run eval on the dev set.")    
    parser.add_argument("--evaluate_during_training", action="store_true",
                        help="Run evaluation during training at each logging step.")

    parser.add_argument("--train_batch_size", default=4, type=int,
                        help="Batch size per GPU/CPU for training.")
    parser.add_argument("--eval_batch_size", default=4, type=int,
                        help="Batch size per GPU/CPU for evaluation.")
    parser.add_argument("--choice", default="best", type=str,
                        help="Model to test")
    parser.add_argument("--type", default="label_train", type=str,
                        help="Model training type")
    parser.add_argument("--size", default="3", type=str,
                        help="Model size")                 
    parser.add_argument("--vocab_size", default=10000, type=int,
                        help="Vocabulary Size.")
    parser.add_argument("--attention_heads", default=8, type=int,
                        help="attention_heads")
    parser.add_argument("--hidden_dim", default=512, type=int,
                        help="Hidden dim of student model.")
    parser.add_argument("--n_layers", default=1, type=int,
                        help="Num of layers in student model.")
    parser.add_argument("--intermediate_size", default=1, type=int)
    parser.add_argument("--gradient_accumulation_steps", type=int, default=1,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument("--learning_rate", default=5e-5, type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--weight_decay", default=0.0, type=float,
                        help="Weight deay if we apply some.")
    parser.add_argument("--adam_epsilon", default=1e-8, type=float,
                        help="Epsilon for Adam optimizer.")
    parser.add_argument("--max_grad_norm", default=1.0, type=float,
                        help="Max gradient norm.")
    parser.add_argument("--max_steps", default=-1, type=int,
                        help="If > 0: set total number of training steps to perform. Override num_train_epochs.")
    parser.add_argument("--warmup_steps", default=0, type=int,
                        help="Linear warmup over warmup_steps.")

    parser.add_argument("--seed", type=int, default=42,
                        help="random seed for initialization")
    parser.add_argument("--epochs", type=int, default=1,
                        help="training epochs")
    
    parser.add_argument("--result_csv_path", default="evaluation_results.csv", type=str,
                        help="Path to the CSV file to save evaluation results.")
    parser.add_argument("--model_name_for_csv", default="unknown_model", type=str,
                        help="Name of the model/experiment for CSV logging (e.g., 'graphcodebert').")
    parser.add_argument("--task", default="CloneDetection", type=str,
                        help="Task Name")

    args = parser.parse_args()

    # Setup CUDA, GPU
    device = torch.device("cuda")
    args.n_gpu = torch.cuda.device_count()

    args.device = device

    # Setup logging
    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",datefmt="%m/%d/%Y %H:%M:%S",level=logging.INFO)
    logger.warning("device: %s, n_gpu: %s",device, args.n_gpu)

    folder = "/".join(args.train_data_file.split("/")[:-1])
    tokenizer_path = os.path.join(folder, "BPE" + "_" + str(args.vocab_size) + ".json")
    tokenizer = Tokenizer.from_file(tokenizer_path)
    # Set seed
    set_seed(args)
    config = RobertaConfig.from_pretrained(args.config_name if args.config_name else args.model_name_or_path)
    config.num_labels=2
    # hidden_dim = 768
    n_labels = 2
    # n_layers = 2

    config = RobertaConfig.from_pretrained("microsoft/graphcodebert-base")

    config.num_labels = n_labels
    config.num_attention_heads = args.attention_heads
    config.hidden_size = args.hidden_dim
    config.intermediate_size = args.intermediate_size
    config.vocab_size = args.vocab_size
    config.num_hidden_layers = args.n_layers
    # config.hidden_dropout_prob = 0.3
    model = Model(RobertaForSequenceClassification(config=config),config,tokenizer,args)   

    logger.info("Training/evaluation parameters %s", args)
    # Training
    if args.do_train:
        train_dataset = TextDataset(tokenizer, args, file_path=args.train_data_file)
        train(args, train_dataset, model, tokenizer)

    # Evaluation
    results = {}
    if args.do_eval:
        checkpoint_prefix = ""
        output_dir = os.path.join(args.output_dir, "{}".format(checkpoint_prefix), args.size, "model.bin")  
        model.load_state_dict(torch.load(output_dir))
        model.to(args.device)
        results = evaluate(args, model, tokenizer)
        
    if args.do_test:
        checkpoint_prefix = ""
        output_dir = os.path.join(args.output_dir, "{}".format(checkpoint_prefix), args.size, "model.bin")  
        model.load_state_dict(torch.load(output_dir))
        model.to(args.device)
        results = test(args, model, tokenizer,best_threshold=0.5)

        # --- CSV Saving Logic ---
        csv_file_exists = os.path.exists(args.result_csv_path)
        with open(args.result_csv_path, 'a', newline='') as csvfile: # 'a' for append mode
            fieldnames = ['name', 'compression_size_MB', 'acc', 'precision', 'recall', 'f1']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not csv_file_exists:
                writer.writeheader() # Write header only if file didn't exist

            writer.writerow({
                'name': args.model_name_for_csv,
                'compression_size_MB': args.size, # args.size is already a string like "3", "25", "50"
                'acc': round(results["test_acc"], 4),
                'precision': round(results["test_precision"], 4),
                'recall': round(results["test_recall"], 4),
                'f1': round(results["test_f1"], 4)
            })
        logger.info(f"Evaluation results saved to {args.result_csv_path}")

    return results


if __name__ == "__main__":
    main()
