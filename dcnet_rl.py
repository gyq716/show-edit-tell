import os
import numpy as np
import json
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import Parameter
from tqdm import tqdm
from torch.utils.data import Dataset
import torch.backends.cudnn as cudnn
import torch.optim
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence, PackedSequence
import torch.utils.data
from cococaption.pycocotools.coco import COCO
from cococaption.pycocoevalcap.eval import COCOEvalCap
from collections import OrderedDict


class COCOTrainDataset(Dataset):

    def __init__(self):
        
        # Captions per image
        self.cpi = 5

        # Load encoded captions (completely into memory)
        with open(os.path.join('caption data','TRAIN_CAPTIONS_coco.json'), 'r') as j:
            self.captions = json.load(j)

        # Load caption lengths (completely into memory)
        with open(os.path.join('caption data', 'TRAIN_CAPLENS_coco.json'), 'r') as j:
            self.caplens = json.load(j)
        
        with open('caption data/TRAIN_names_coco.json', 'r') as j:
            self.names = json.load(j)
            
        with open('caption data/CAPUTIL_train.json', 'r') as j:
            self.caption_util = json.load(j)

        # Total number of datapoints
        self.dataset_size = len(self.captions)

    def __getitem__(self, i):
        """
        returns:
        caption: the ground-truth caption of shape (batch_size, max_length)
        caplen: the valid length (without padding) of the ground-truth caption of shape (batch_size,1)
        previous_caption: the encoded caption of the previous model of shape (batch_size, max_length)
        previous_caption_length: the valid length (without padding) of the previous caption of shape (batch_size,1)
        """
        # The Nth caption corresponds to the (N // captions_per_image)th image
        img_name = self.names[i // self.cpi]
        
        caption = torch.LongTensor(self.captions[i])
        caplen = torch.LongTensor([self.caplens[i]])
        
        previous_caption = torch.LongTensor(self.caption_util[img_name]['encoded_previous_caption'])
        prev_caplen = torch.LongTensor(self.caption_util[img_name]['previous_caption_length'])
        all_captions = torch.LongTensor(self.captions[((i // self.cpi) * self.cpi):(((i // self.cpi) * self.cpi) + self.cpi)])
        
        return caption, caplen, previous_caption, prev_caplen, all_captions

    def __len__(self):
        return self.dataset_size
    
    
class COCOValidationDataset(Dataset):

    def __init__(self):
        
        self.cpi = 5
        
        with open('caption data/VAL_names_coco.json', 'r') as j:
            self.names = json.load(j)
            
        with open('caption data/CAPUTIL_val.json', 'r') as j:
            self.caption_util = json.load(j)

        # Total number of datapoints
        self.dataset_size = len(self.names)

    def __getitem__(self, i):
        """
        returns:
        previous_caption: the encoded caption of the previous model of shape (batch_size, max_length)
        image_id: the respective id for the image of shape (batch_size, 1)
        previous_caption_length: the valid length (without padding) of the previous caption of shape (batch_size,1)
        """
        img_name = self.names[i]
        
        previous_caption = torch.LongTensor(self.caption_util[img_name]['encoded_previous_caption'])
        image_id = torch.LongTensor([self.caption_util[img_name]['image_ids']])
        prev_caplen = torch.LongTensor(self.caption_util[img_name]['previous_caption_length'])
        
        return image_id, previous_caption, prev_caplen

    def __len__(self):
        return self.dataset_size


def save_checkpoint(epoch, epochs_since_improvement, dae_mse, dae_mse_optimizer, cider, is_best):

    state = {'epoch': epoch,
             'epochs_since_improvement': epochs_since_improvement,
             'cider': cider,
             'dae_mse': dae_mse,
             'dae_mse_optimizer': dae_mse_optimizer}
    
    filename = 'checkpoint_' + str(epoch) + '.pth.tar'
    torch.save(state, filename)
    # If this checkpoint is the best so far, store a copy so it doesn't get overwritten by a worse checkpoint
    if is_best:
        torch.save(state, 'BEST_' + filename)
        
def set_learning_rate(optimizer, lr):

    for param_group in optimizer.param_groups:
        param_group['lr'] = lr
    print("The new learning rate is %f\n" % (optimizer.param_groups[0]['lr'],))
        

def adjust_learning_rate(optimizer, shrink_factor):

    print("\nDECAYING learning rate.")
    for param_group in optimizer.param_groups:
        param_group['lr'] = param_group['lr'] * shrink_factor
    print("The new learning rate is %f\n" % (optimizer.param_groups[0]['lr'],))


class Embedding(nn.Module):

    def __init__(self, word_map, emb_file, emb_dim, load_glove_embedding = False):
        """
        word_map: the wordmap file constructed
        emb_file: the .txt file for the glove embedding weights 
        """
        super(Embedding, self).__init__()
        
        self.emb_dim = emb_dim
        self.load_glove_embedding = load_glove_embedding
        
        if self.load_glove_embedding: 
            print("Loading GloVe...")
            with open(emb_file, 'r') as f:
                self.emb_dim = len(f.readline().split(' ')) - 1
            print("Done Loading GLoVe")
                
        self.emb_file = emb_file
        self.word_map = word_map
        self.embedding = nn.Embedding(len(word_map), self.emb_dim)  # embedding layer
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(0.5)
        
        if self.load_glove_embedding:
            self.load_embeddings()   
        
    def load_embeddings(self, fine_tune = True):

        vocab = set(self.word_map.keys())

        # Create tensor to hold embeddings, initialize
        embeddings = torch.FloatTensor(len(vocab), self.emb_dim)
        bias = np.sqrt(3.0 / embeddings.size(1))
        torch.nn.init.uniform_(embeddings, -bias, bias)   # initialize embeddings. Unfound words in the word_map are initialized

        # Read embedding file
        for line in open(self.emb_file, 'r', encoding="utf8"):
            line = line.split(' ')
            emb_word = line[0]
            embedded_word = list(map(lambda t: float(t), filter(lambda n: n and not n.isspace(), line[1:])))
            # Ignore word if not in vocab
            if emb_word not in vocab:
                continue   # go back and continue the loop
            embeddings[self.word_map[emb_word]] = torch.FloatTensor(embedded_word)

        self.embedding.weight = nn.Parameter(embeddings)
        
        if not fine_tune:
            for p in self.embedding.parameters():
                p.requires_grad = False
                
    def forward(self, x):
        if self.load_glove_embedding:
            return self.embedding(x)
        else:
            out = self.embedding(x)
            out = self.relu(out)
            out = self.dropout(out)
            return out


class CaptionEncoder(nn.Module):
    def __init__(self, vocab_size, emb_dim, enc_hid_dim, concat_output_dim, embed):
        super(CaptionEncoder,self).__init__()
        
        self.vocab_size = vocab_size
        self.emb_dim = emb_dim
        self.enc_hid_dim = enc_hid_dim
        self.embed = embed
        self.lstm_encoder = nn.LSTM(emb_dim, enc_hid_dim, batch_first = True, bidirectional = True)
        self.concat = nn.Linear(enc_hid_dim * 2, concat_output_dim)

    def forward(self, src, src_len):
        """
        src: the sentence to encode of shape (batch_size, seq_length) of type Long
        src_len: long tensor that contains the lengths of each sentence in the batch of shape (batch_size, 1) of type Long
        """
        embedded = self.embed(src)  # (batch_size, seq_length, emb_dim)
        src_len = src_len.squeeze(1).tolist()
        
        packed_embedded = pack_padded_sequence(embedded, 
                                               src_len, 
                                               batch_first = True,
                                               enforce_sorted = False) # or sort then set to true (default: true)
                
        packed_outputs, hidden = self.lstm_encoder(packed_embedded)  #hidden of shape (2, batch_size, hidden_size)
        # packed sequence containing all hidden states                       
        # hidden is now from the final non-padded element in the batch
        # outputs of shape (batch_size, seq_length, hidden_size * 2)
        outputs, _ = pad_packed_sequence(packed_outputs,
                                         batch_first=True) 
        prev_cap_mask = ((outputs.sum(2))!=0).float()
        #outputs is now a non-packed sequence, all hidden states obtained when the input is a pad token are all zeros
        concat_hidden = torch.cat((hidden[0][-2,:,:], hidden[0][-1,:,:]), dim = 1)  # (batch_size, hidden_size * 2)
        final_hidden = torch.tanh(self.concat(concat_hidden))   # (batch_size, concat_output_dim)
        return outputs, final_hidden, prev_cap_mask

class CaptionAttention(nn.Module):

    def __init__(self, caption_features_dim, decoder_dim, attention_dim):

        super(CaptionAttention, self).__init__()
        self.cap_features_att = nn.Linear(caption_features_dim * 2, attention_dim) 
        self.cap_decoder_att = nn.Linear(decoder_dim, attention_dim) 
        self.cap_full_att = nn.Linear(attention_dim, 1)

    def forward(self, caption_features, decoder_hidden, prev_caption_mask):
        """
        caption features of shape: (batch_size, max_seq_length, hidden_size*2) (hidden_size = caption_features_dim)
        prev_caption_mask of shape: (batch_size, max_seq_length)
        decoder_hidden is the current output of the decoder LSTM of shape (batch_size, decoder_dim)
        text_chunk is the output of the word gating of shape (batch_size, 1024)
        """
        att1_c = self.cap_features_att(caption_features)  # (batch_size, max_words, attention_dim)
        att2_c = self.cap_decoder_att(decoder_hidden)  # (batch_size, attention_dim)
        att_c = self.cap_full_att(torch.tanh(att1_c + att2_c.unsqueeze(1))).squeeze(2)  # (batch_size, max_words)
        # Masking for zero pads for attention computation
        att_c = att_c.masked_fill(prev_caption_mask == 0, -1e10)   # (batch_size, max_words) * (batch_size, max_words)
        alpha_c = F.softmax(att_c, dim = 1)  # (batch_size, max_words)
        
        context = (caption_features * alpha_c.unsqueeze(2)).sum(dim=1)  # (batch_size, caption_features_dim)
        
        return context


class DAE(nn.Module):

    def __init__(self, 
                 word_map,  
                 emb_file,
                 decoder_dim = 1024, 
                 attention_dim = 512,
                 caption_features_dim = 512, 
                 emb_dim = 1024):
        
        super(DAE, self).__init__()
        
        self.vocab_size = len(word_map)
        self.attention_lstm = nn.LSTMCell(emb_dim * 3, decoder_dim)
        self.language_lstm = nn.LSTMCell(emb_dim * 2, decoder_dim)
        self.embed = Embedding(word_map, emb_file, emb_dim, load_glove_embedding = False)
        self.caption_encoder = CaptionEncoder(len(word_map), emb_dim, caption_features_dim, 
                                              caption_features_dim * 2, self.embed)
        self.caption_attention = CaptionAttention(caption_features_dim, decoder_dim, attention_dim)
        self.fc = nn.Linear(decoder_dim, len(word_map))
        self.tanh = nn.Tanh()
        self.decoder_dim = decoder_dim
        self.dropout = nn.Dropout(0.5)
        
    def init_hidden_state(self,batch_size):

        h = torch.zeros(batch_size,self.decoder_dim).to(device)  # (batch_size, decoder_dim)
        c = torch.zeros(batch_size,self.decoder_dim).to(device)
        return h, c

    def forward(self, word_map, encoded_previous_captions, previous_cap_length, sample_max, sample_rl):
        """
        encoded captions of shape: (batch_size, max_caption_length)
        caption_lengths of shape: (batch_size, 1)
        encoded_previous_captions: encoded previous captions to be passed to the LSTM encoder of shape: (batch_size, max_caption_length)
        previous_caption_lengths of shape: (batch_size, 1)
        prev_caption_mask of shape (batch_size, max_words)
        """
        batch_size = encoded_previous_captions.size(0)
        max_len = 18
        seq = torch.zeros(batch_size, max_len, dtype=torch.long).to(device)
        seqLogprobs = torch.zeros(batch_size, max_len).to(device)
        start_idx = word_map['<start>']
        it = torch.LongTensor(batch_size).to(device)   # (batch_size) 
        it[:] = start_idx
        h1, c1 = self.init_hidden_state(batch_size)  # (batch_size, decoder_dim)
        h2, c2 = self.init_hidden_state(batch_size)  # (batch_size, decoder_dim)
        previous_encoded, final_hidden, prev_cap_mask = self.caption_encoder(encoded_previous_captions, previous_cap_length)
        
        for timestep in range(max_len + 1):
            embeddings = self.embed(it) 
            topdown_input = torch.cat([embeddings,final_hidden, h2],dim=1)
            h1,c1 = self.attention_lstm(topdown_input, (h1, c1))
            attend_cap = self.caption_attention(previous_encoded, h1, prev_cap_mask)
            language_input = torch.cat([h1, attend_cap], dim = 1)
            h2,c2 = self.language_lstm(language_input, (h2, c2))
            pt = self.fc(self.dropout(h2)) 
            logprobs = F.log_softmax(pt, dim=1)
            
            if timestep == max_len:
                break
                
            if sample_max: # Greedy decoding
                sampleLogprobs, it = torch.max(logprobs, 1)
                it = it.view(-1).long()

            if sample_rl:   # Sampling from multinomial for self-critical
                prob_prev = torch.exp(logprobs)     # fetch prev distribution (softmax)
                it = torch.multinomial(prob_prev, 1)
                sampleLogprobs = logprobs.gather(1, it) # gather the logprobs at sampled positions
                it = it.view(-1).long() # flatten indices for saving in tensor
                
            # Replace <end> token (if there is) with 0. Otherwise, a lot to change in ruotianluo code
            it = it.clone()
            it[it == word_map['<end>']] = 0
            
            # If all batches predict the <end> token, then stop looping
            if timestep == 0:
                unfinished = it > 0
            else:
                unfinished = unfinished * (it > 0)
                
            it = it * unfinished.type_as(it)
            seq[:,timestep] = it
            seqLogprobs[:,timestep] = sampleLogprobs.view(-1)
            
            # quit loop if all sequences have finished
            if unfinished.sum() == 0:
                break
                
        return seq, seqLogprobs
    
class DAEWithAR(nn.Module):
    """
    DAE with MSE Optimization
    """
    def __init__(self):
        super(DAEWithAR, self).__init__()
        
        model = torch.load('BEST_checkpoint_3_dae.pth.tar')
        self.dae = model['dae']
        decoder_dim = self.dae.decoder_dim
        self.affine_hidden = nn.Linear(decoder_dim, decoder_dim)
        
    def forward(self, *args, **kwargs):
        return self.dae(*args, **kwargs)
    
    
class RewardCriterion(nn.Module):
    def __init__(self):
        super(RewardCriterion, self).__init__()

    def forward(self, sample_logprobs, seq, reward):
        
        sample_logprobs = sample_logprobs.view(-1)   # (batch_size * max_len)
        reward = reward.view(-1)
        # set mask elements for all <end> tokens to 0 
        mask = (seq>0).float()                        # (batch_size, max_len)
        
        # account for the <end> token in the mask. We do this by shifting the mask one timestep ahead
        mask = torch.cat([mask.new(mask.size(0), 1).fill_(1), mask[:, :-1]], 1)
        
        if not mask.is_contiguous():
            mask = mask.contiguous()
        
        mask = mask.view(-1)
        output = - sample_logprobs * reward * mask
        output = torch.sum(output) / torch.sum(mask)
        return output
    
import sys
sys.path.append("cider")
from pyciderevalcap.ciderD.ciderD import CiderD
sys.path.append("coco-caption")

CiderD_scorer = None

def init_scorer(cached_tokens):
    global CiderD_scorer
    CiderD_scorer = CiderD_scorer or CiderD(df=cached_tokens)
    
def preprocess_gd(allcaps, word_map):
    """
    allcaps: Long tensor of shape (batch_size, 5, max_len)
    """
    ground_truth = []
    for j in range(allcaps.shape[0]):
        # when training with RL, no need to sort the batches as we did in cross-entropy training, since we don't feed
        # the ground truth encoded captions to the LSTM language model
        img_caps = allcaps[j].tolist()   # list of length 5
        img_captions = list(map(lambda c: [w for w in c if w not in {word_map['<start>'], word_map['<pad>']}], img_caps)) 
        # 0 will get removed later in array_to_str
        img_captions_z = list(map(lambda c:[w if w!=word_map['<end>'] else 0 for w in c], img_captions)) 
        ground_truth.append(img_captions_z)
    return ground_truth  # list of length batch_size, each element in this list contains the 5 captions in another list (3D list)

def array_to_str(arr):
    out = ''
    for i in range(len(arr)):
        out += str(arr[i]) + ' '
        # If reached end token
        if arr[i] == 0:   # not word_map['<end>']. Remember we replaced word_map['<end>'] with 0 in the sample function
            break
    return out.strip()

def get_self_critical_reward(gen_result, greedy_res, ground_truth, cider_weight = 1):
    
    # ground_truth is the 5 ground truth captions for a mini-batch, which can be aquired from the preprocess_gd function
    #[[c1, c2, c3, c4, c5], [c1, c2, c3, c4, c5],........]. Note that c is a caption placed in a list
    # len(ground_truth) = batch_size. Already duplicated the ground truth captions in dataloader
    
    batch_size = gen_result.size(0)  
    
    res = OrderedDict()
    gen_result = gen_result.data.cpu().numpy()   # (batch_size, max_len)
    greedy_res = greedy_res.data.cpu().numpy()   # (batch_size, max_len)
    
    for i in range(batch_size):
        # change to string for evaluation purpose 
        res[i] = [array_to_str(gen_result[i])]
        
    for i in range(batch_size):
        # change to string for evaluation purpose
        res[batch_size + i] = [array_to_str(greedy_res[i])]

    gts = OrderedDict()
    for i in range(len(ground_truth)):
        gts[i] = [array_to_str(ground_truth[i][j]) for j in range(len(ground_truth[i]))]
    
    # 2 is because one is for the sampling and one for greedy decoding
    res_ = [{'image_id':i, 'caption': res[i]} for i in range(2 * batch_size)] 
    # the number of ground-truth captions for each image stay the same as above. Duplicate for the sampling and greedy
    gts = {i: gts[i % batch_size] for i in range(2 * batch_size)}
    _, cider_scores = CiderD_scorer.compute_score(gts, res_)

    scores = cider_weight * cider_scores
    scores = scores[:batch_size] - scores[batch_size:]
    rewards = np.repeat(scores[:, np.newaxis], gen_result.shape[1], 1)    # gen_result.shape[1] = max_len
    rewards = torch.from_numpy(rewards).float()

    return rewards

     
def train(train_loader, dae_ar, criterion, dae_ar_optimizer, epoch, word_map):

    dae_ar.train()  # train mode (dropout and batchnorm is used)

    sum_rewards = 0
    count = 0

    for i, (_, _, previous_caption, prev_caplen, allcaps) in enumerate(train_loader):
        
        samples = previous_caption.shape[0]
       
        previous_caption = previous_caption.to(device)
        prev_caplen = prev_caplen.to(device)

        
        dae_ar_optimizer.zero_grad()
        dae_ar.eval()
        with torch.no_grad():
            greedy_res, _ = dae_ar(word_map, previous_caption, prev_caplen, sample_max = True, sample_rl = False)
        dae_ar.train()
        seq_gen, seqLogprobs = dae_ar(word_map, previous_caption, prev_caplen, sample_max = False, sample_rl = True)
        ground_truth = preprocess_gd(allcaps, word_map)
        rewards = get_self_critical_reward(seq_gen, greedy_res, ground_truth, cider_weight = 1)
        loss = criterion(seqLogprobs, seq_gen, rewards.to(device))
        
        loss.backward()
        torch.nn.utils.clip_grad_norm_(filter(lambda p: p.requires_grad, dae_ar.parameters()), 0.25)
        dae_ar_optimizer.step()
        
        sum_rewards += torch.mean(rewards[:,0]) * samples
        count += samples
        
        # Print status
        if i % print_freq == 0:
            print('Epoch: [{}][{}/{}]\tAverage Reward: {:.3f}'.format(epoch, i, len(train_loader), sum_rewards/count))


def evaluate(loader, dae_ar, beam_size, epoch, word_map):
    
    vocab_size = len(word_map)
    dae_ar.eval()
    results = []
    rev_word_map = {v: k for k, v in word_map.items()}
    
    # For each image
    for i, (image_id, previous_caption, prev_caplen) in enumerate(tqdm(loader, desc="EVALUATING AT BEAM SIZE " + str(beam_size))):

        k = beam_size
        infinite_pred = False

        # Move to GPU device, if available
        encoded_previous_captions = previous_caption.to(device) 
        prev_caplen = prev_caplen.to(device) 
        image_id = image_id.to(device)  # (1,1)
        
        previous_encoded, final_hidden, prev_caption_mask = dae_ar.dae.caption_encoder(encoded_previous_captions, prev_caplen)
        
        # Expand all
        previous_encoded = previous_encoded.expand(k, -1, -1)
        prev_cap_mask = prev_caption_mask.expand(k, -1)
        final_hidden = final_hidden.expand(k,-1)
        
        # Tensor to store top k previous words at each step; now they're just <start>
        k_prev_words = torch.LongTensor([[word_map['<start>']]] * k).to(device)  # (k, 1)

        # Tensor to store top k sequences; now they're just <start>
        seqs = k_prev_words  # (k, 1)

        # Tensor to store top k sequences' scores; now they're just 0
        top_k_scores = torch.zeros(k, 1).to(device)  # (k, 1)

        # Lists to store completed sequences and scores
        complete_seqs = list()
        complete_seqs_scores = list()

        # Start decoding
        step = 1
        h1, c1 = dae_ar.dae.init_hidden_state(k)  # (batch_size, decoder_dim)
        h2, c2 = dae_ar.dae.init_hidden_state(k)

        # s is a number less than or equal to k, because sequences are removed from this process once they hit <end>
        while True:

            embeddings = dae_ar.dae.embed(k_prev_words).squeeze(1)        
            topdown_input = torch.cat([embeddings, final_hidden, h2],dim=1)
            h1,c1 = dae_ar.dae.attention_lstm(topdown_input, (h1, c1))
            attend_cap = dae_ar.dae.caption_attention(previous_encoded, h1, prev_cap_mask)
            language_input = torch.cat([h1, attend_cap], dim = 1)
            h2,c2 = dae_ar.dae.language_lstm(language_input, (h2, c2))
            scores = dae_ar.dae.fc(h2)  
            scores = F.log_softmax(scores, dim=1)

            # Add
            scores = top_k_scores.expand_as(scores) + scores  # (s, vocab_size)

            # For the first step, all k points will have the same scores (since same k previous words, h, c)
            if step == 1:
                top_k_scores, top_k_words = scores[0].topk(k, 0, True, True)  # (s)
            else:
                # Unroll and find top scores, and their unrolled indices
                top_k_scores, top_k_words = scores.view(-1).topk(k, 0, True, True)  # (s)

            # Convert unrolled indices to actual indices of scores
            prev_word_inds = top_k_words / vocab_size  # (s)
            next_word_inds = top_k_words % vocab_size  # (s)

            # Add new words to sequences
            seqs = torch.cat([seqs[prev_word_inds], next_word_inds.unsqueeze(1)], dim=1)  # (s, step+1)

            # Which sequences are incomplete (didn't reach <end>)?
            incomplete_inds = [ind for ind, next_word in enumerate(next_word_inds) if next_word != word_map['<end>']]
            complete_inds = list(set(range(len(next_word_inds))) - set(incomplete_inds))

            # Set aside complete sequences
            if len(complete_inds) > 0:
                complete_seqs.extend(seqs[complete_inds].tolist())
                complete_seqs_scores.extend(top_k_scores[complete_inds])
            k -= len(complete_inds)  # reduce beam length accordingly

            # Proceed with incomplete sequences
            if k == 0:
                break
                
            seqs = seqs[incomplete_inds]
            h1 = h1[prev_word_inds[incomplete_inds]]
            c1 = c1[prev_word_inds[incomplete_inds]]
            h2 = h2[prev_word_inds[incomplete_inds]]
            c2 = c2[prev_word_inds[incomplete_inds]]
            previous_encoded = previous_encoded[prev_word_inds[incomplete_inds]]
            prev_cap_mask = prev_cap_mask[prev_word_inds[incomplete_inds]]
            final_hidden = final_hidden[prev_word_inds[incomplete_inds]]
            top_k_scores = top_k_scores[incomplete_inds].unsqueeze(1)
            k_prev_words = next_word_inds[incomplete_inds].unsqueeze(1)

            # Break if things have been going on too long
            if step > 50:
                infinite_pred = True
                break
            step += 1

        if infinite_pred is not True:
            i = complete_seqs_scores.index(max(complete_seqs_scores))
            seq = complete_seqs[i]
        else:
            seq = seqs[0][:18]
            seq = [seq[i].item() for i in range(len(seq))]
            
        # Construct Sentence
        sen_idx = [w for w in seq if w not in {word_map['<start>'], word_map['<end>'], word_map['<pad>']}]
        sentence = ' '.join([rev_word_map[sen_idx[i]] for i in range(len(sen_idx))])
        item_dict = {"image_id": image_id.item(), "caption": sentence}
        results.append(item_dict)
        
    print("Calculating Evalaution Metric Scores......\n")
    resFile = 'cococaption/results/captions_val2014_results_' + str(epoch) + '.json' 
    evalFile = 'cococaption/results/captions_val2014_eval_' + str(epoch) + '.json' 
    # Calculate Evaluation Scores
    with open(resFile, 'w') as wr:
        json.dump(results,wr)
        
    coco = COCO(annFile)
    cocoRes = coco.loadRes(resFile)
    # create cocoEval object by taking coco and cocoRes
    cocoEval = COCOEvalCap(coco, cocoRes)
    # evaluate on a subset of images
    # please remove this line when evaluating the full validation set
    cocoEval.params['image_id'] = cocoRes.getImgIds()
    # evaluate results
    cocoEval.evaluate()    
    # Save Scores for all images in resFile
    with open(evalFile, 'w') as w:
        json.dump(cocoEval.eval, w)

    return cocoEval.eval['CIDEr'], cocoEval.eval['Bleu_4']


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
cudnn.benchmark = True  # set to true only if inputs to model are fixed size; otherwise lot of computational overhead
start_epoch = 0
epochs = 50  # number of epochs to train for (if early stopping is not triggered)
epochs_since_improvement = 0  # keeps track of number of epochs since there's been an improvement in validation BLEU
batch_size = 60
best_cider = 0.
print_freq = 100  # print training/validation stats every __ batches
checkpoint = 'dcnet.tar' # path to checkpoint, None if none
annFile = 'cococaption/annotations/captions_val2014.json'  # Location of validation annotations
emb_file = 'glove.6B.300d.txt'
cached_tokens =  'coco-train-idxs'

# Read word map
with open('caption data/WORDMAP_coco.json', 'r') as j:
    word_map = json.load(j)
    
rev_word_map = {v: k for k, v in word_map.items()}
    

checkpoint = torch.load(checkpoint)
start_epoch = checkpoint['epoch'] + 1
epochs_since_improvement = checkpoint['epochs_since_improvement']
best_cider = checkpoint['cider']
print(best_cider)
dae_ar = checkpoint['dae_ar']
dae_ar_optimizer = checkpoint['dae_ar_optimizer']

dae_ar = dae_ar.to(device)

for param in dae_ar.affine_hidden.parameters():
    param.requires_grad = False


# Loss functions
criterion = RewardCriterion().to(device)


train_loader = torch.utils.data.DataLoader(COCOTrainDataset(),
                                           batch_size = batch_size, 
                                           shuffle=True, 
                                           pin_memory=True)

val_loader = torch.utils.data.DataLoader(COCOValidationDataset(),
                                         batch_size = 1,
                                         shuffle=True, 
                                         pin_memory=True)

# Epochs
for epoch in range(start_epoch, epochs):
    
    if epoch == start_epoch:   # only at the starting epoch of self-critical. Then comment out
        set_learning_rate(dae_ar_optimizer, 5e-5)

    if epochs_since_improvement > 0:
        adjust_learning_rate(dae_ar_optimizer, 0.5)
        
    init_scorer(cached_tokens)
        
    # One epoch's training
    train(train_loader=train_loader,
          dae_ar=dae_ar,
          criterion = criterion, 
          dae_ar_optimizer=dae_ar_optimizer,
          epoch=epoch, 
          word_map = word_map)

    # One epoch's validation
    recent_cider, recent_bleu4 = evaluate(loader = val_loader, 
                                          dae_ar = dae_ar, 
                                          beam_size = 3, 
                                          epoch = epoch, 
                                          word_map = word_map)

    # Check if there was an improvement
    is_best = recent_cider > best_cider
    best_cider = max(recent_cider, best_cider)
    if not is_best:
        epochs_since_improvement += 1
        print("\nEpochs since last improvement: %d\n" % (epochs_since_improvement,))
    else:
        epochs_since_improvement = 0

    # Save checkpoint
    save_checkpoint(epoch, epochs_since_improvement, dae_ar, dae_ar_optimizer, recent_cider, is_best)


